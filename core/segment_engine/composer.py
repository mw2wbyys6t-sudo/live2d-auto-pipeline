#!/usr/bin/env python3
"""
Pygmalion Studio - Layer Composition & Ordering

Given per-part boolean masks and the original image, this module extracts
transparent RGBA layers, applies amodal completion for occluded regions,
saves each layer as a PNG, reorders them to the standard Live2D draw order,
emits layer metadata JSON, and builds an occlusion map.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger
from core.segment_engine import face_detect
from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.part_split import (
    DEGRADATION_CODES,
    attach_depths,
    refine_part_masks,
    split_depth_parts,
    split_eye_parts,
    split_garment_details,
    split_hair_parts,
    split_limb_parts,
)

log = get_logger("segment.composer")


class LayerComposer:
    """Compose per-part masks into ordered, exported Live2D layers.

    The standard draw order (back-to-front) follows conventional Live2D
    Cubism rigging: scalp/hair behind, then face features, clothing, then
    foreground hair and effects.
    """

    STANDARD_LAYER_ORDER: List[str] = [
        # --- l2d_sam_iter2 的 19 类部位名 ---
        # 这些名字来自 See-through 的部位解析模型，跟本表原有的语义名字并存。
        # 不列进来的名字会被 reorder_layers 按字母序追加到最后（=画在所有层之上），
        # 遮挡关系也会反过来，所以每个 19 类名都必须在正确位置显式出现。
        "wings",
        "tail",
        "scalp",
        "hair",          # 19 类只给一整块头发，按后发处理（见 layers52 的 primary=1）
        "hair_back",
        # 后发左右两束（split_hair_parts 自适应拆出）：与后发同层，必须显式列名，
        # 否则会按字母序落到最前 = 画在脸和衣服之上。
        # 每侧再分出的第三束（split_hair_side_strands 的 `_inner`/`_outer`）排在
        # 父束**之前**：父束登记在 AMODAL_PARTS 里，补画会把它前面的同族层填满；
        # 排在后面就会被父束的补画覆盖，拆了也白拆。
        "hair_back_left_inner", "hair_back_left_outer",
        "hair_back_right_inner", "hair_back_right_outer",
        "hair_back_left", "hair_back_right",
        "hair_mid",
        # 刘海（hair_front / hair_bangs）**不在这里**：见下方眼睑之后的位置。
        # 原因：脸层在刘海位置是镂空的（分出来的脸只有可见皮肤），所以"刘海"原本
        # 是靠脸的洞透出来的。闭眼图一旦给那些洞填色，刘海就被涂成皮肤（实测
        # 1206 px）。把刘海排到脸/闭眼图之后，它直接盖在上面，与 `preview.png` 的
        # 逐像素误差反而更小（MAE 1.5767 → 1.5695）。
        # 脸底层必须在**五官之前**（列表越靠后 = 画得越靠前）。
        #
        # 历史顺序把 face_base 排在 eyebrows/eyes 之后：那时 face 层里复制了眼睛
        # 像素，谁在前都看不出来。脸层被清成纯皮肤后（见 FACE_PARTS），顺序立刻
        # 显形——实测眼睛被脸盖成几块碎片。
        "face_base",
        "face",
        # 19 类的耳朵（52 层里排在脸之后、五官之前）
        "ears",
        # 腮红：合成层，画在脸皮上、五官之下。静止时**不可见**（不透明度 0），
        # 由 ParamCheek 淡入 —— 见 REST_INVISIBLE_PARTS。
        "cheek",
        # 闭眼图：合成层（见 synthesize_face_geometry）。必须排在眼球**之前**
        # （列表越靠后 = 画得越靠前）—— 闭眼图是"眼皮落下后露出来的那张脸"，
        # 画在眼球下面，眼球向上收拢时才露出来。
        "eyelid_left", "eyelid_right",
        # 五官：真实分层器产出的名字（eyewhite_* / irides_* / eyebrow_*）都要列上，
        # 否则它们会落到"未列出→按字母序追加到最后"，被画到头发和衣服之上。
        "eyes", "eyes_left", "eyes_right",
        "eyewhite_left", "eyewhite_right",
        "irides_left", "irides_right",
        # P1 眼细节（split_eye_details）：瞳孔在虹膜之前/同层，高光压在最上层。
        "eye_pupil_left", "eye_pupil_right",
        "eye_highlight_left", "eye_highlight_right",
        "eyelash_left", "eyelash_right",
        # 上下睫毛（split_eye_details）：上睫毛在上、下睫毛贴下眼睑。
        "eyelash_upper_left", "eyelash_upper_right",
        "eyelash_lower_left", "eyelash_lower_right",
        # 眼泪：合成层，画在眼睛之上。静止时**不可见**（不透明度 0），
        # 由 ParamTears × ParamTearsL/R 淡入。
        "tear_left", "tear_right",
        # 19 类的眼镜：压在眼睛之上、刘海之下。
        "eyewear",
        # 刘海：必须排在闭眼图之后（画在闭眼图前），否则闭眼图的补色会盖住刘海。
        # 分束后的 `*_left` / `*_right` 与母层相邻（split_hair_bangs_strands）。
        "hair_front_left", "hair_front_right",
        "hair_bangs_left", "hair_bangs_right",
        "hair_front", "hair_bangs",
        # 19 类的头饰：帽子压在头发之上。
        "headwear",
        "eyebrows", "eyebrow_L", "eyebrow_R",
        "nose", "nose_mouth",
        # 口内：合成层，必须在嘴线**之前**（先画腔体，再画嘴唇）。
        "mouth_cavity",
        # P1 口部细节（split_mouth_details）：腔体 -> 舌 -> 齿 -> 唇 -> 嘴线。
        "tongue", "teeth", "lip_lower", "lip_upper",
        "mouth",
        "neck",
        "clothes_top",
        # 19 类的上衣：与 clothes_top 同层，二者不同时出现（后端二选一）
        "topwear",
        "clothes_inner",
        "waistband",
        "collar",
        # 19 类的颈饰：围巾/领带压在领口之上
        "neckwear",
        "sleeve_L",
        "sleeve_R",
        "cuff_L",
        "cuff_R",
        "arms",
        "arm_L",
        "arm_R",
        # 肘部分段（split_limb_segments）：上臂在后、小臂在前，与手臂同族相邻。
        "arm_upper_L", "arm_lower_L",
        "arm_upper_R", "arm_lower_R",
        "hands",
        "hand_L",
        "hand_R",
        # 19 类的手套/袖套：压在手臂与手之上
        "handwear",
        # 裙内层（split_skirt_inner）：在内衬位置、被裙子外轮廓挡住，与裙子同族。
        "skirt_inner",
        "skirt",
        "skirt_back",
        "bottomwear_back",
        "clothes_bottom",
        "skirt_front",
        "bottomwear_front",
        "skirt_hem_L",
        "skirt_hem_R",
        "skirt_hem",
        "legs",
        "leg_L",
        "leg_R",
        # 膝盖分段（split_leg_segments）：大腿在后、小腿在前，与腿同族相邻。
        "thigh_L", "calf_L",
        "thigh_R", "calf_R",
        # 19 类的下装：压住裸腿、被袜子与鞋压住
        "bottomwear",
        "legwear",
        "sock_L",
        "sock_R",
        # 19 类的鞋
        "footwear",
        "boot_L",
        "boot_R",
        "boot_cuff_L",
        "boot_cuff_R",
        # 19 类的耳饰：与配饰同层
        "earwear",
        "accessories",
        "tail_ears",
        "effects",
        # 19 类的道具：最前排
        "objects",
    ]

    # Mask names that should receive amodal completion (regions typically
    # occluded by other parts in front of them).
    #:
    #: 19 类部位名同样要在这里登记，否则后面被压住的身体/衣服不会补画，
    #: 一做呼吸/摆臂就会从层缝里露出背景。前排层（handwear/footwear 等）不登记，
    #: 它们是被别的层遮挡的对象、不是需要补画的底层。
    AMODAL_PARTS = {
        "hair_back", "hair_back_left", "hair_back_right", "hair_mid",
        "clothes_top", "clothes_inner", "neck",
        # l2d_sam_iter2
        "hair", "topwear", "bottomwear", "legwear", "neckwear",
    }

    #: 脸底层。它必须是**干净皮肤**——下面的五官由各自的网格驱动（眨眼、说话），
    #: 脸层里若留着同样的五官像素，那些参数就会"改了但看不出来"。
    #: 实测：`face.png` 在 (308,155) 的像素与 `irides_left.png` 完全相同，
    #: 于是 ParamEyeLOpen=0 时眼睛确实被压扁了，脸层又把瞳孔原样露出来。
    FACE_PARTS = ("face", "face_base")

    #: 画在脸上的五官层。导出脸底层时这些区域的像素要被肤色补掉。
    #:
    #: 19 类的 ``eyes`` 也要列上：l2d_sam_iter2 实测在同一张立绘上
    #: ``face ∩ eyes`` = 539px（占眼睛的 32%），不擦掉的话眨眼时脸上会留着一副
    #: 没被压扁的眼睛。
    FACE_FEATURE_PARTS = (
        "eyes", "eyes_left", "eyes_right", "eyebrows", "nose", "mouth",
        # split_limb_parts 会把 19 类的 ``eyes`` 按中线改成 ``eye_white_L/R``；
        # 若随后眼白/瞳孔拆分失败，导出的就是这两个带大写后缀的名字。
        "eye_white_L", "eye_white_R",
        "eyewhite_left", "eyewhite_right", "irides_left", "irides_right",
        "eyelash_left", "eyelash_right", "eye_left", "eye_right",
        "eye_iris_left", "eye_iris_right", "eye_white_left", "eye_white_right",
    )

    RIG_REQUIRED_PARTS = frozenset({
        "hand_L", "hand_R", "eyebrow_L", "eyebrow_R",
    })

    #: 补掉五官时向外扩的像素数：五官边缘有 1~2px 抗锯齿，不扩会留一圈深色描边，
    #: 眨眼/闭嘴时那一圈会露出来，看起来像"眼睛还在"。
    FEATURE_ERASE_MARGIN = 2

    #: 可见面积低于画布这个比例的层直接丢弃。实测残留过 74px 的 skirt_hem：
    #: 它既看不见，又白占一次 draw call 和一套永远空转的绑定。
    MIN_LAYER_AREA_RATIO = 0.0005

    #: 掩码闭运算半径（像素）。SAM 会把同一条缎带/薄纱切成相距 1~2px 的碎片，
    #: 闭运算把它们接回来；半径不能再大，否则会把不相干的元素粘在一起。
    MASK_CLOSE_RADIUS = 1

    #: 非五官层的孤立碎片下限（占该层总面积的比例与绝对像素取大者）。
    DUST_AREA_RATIO = 0.02
    DUST_MIN_PX = 24

    #: 擦五官时"墨线"的亮度上限：睫毛/眼线/眉线都比它暗。
    FEATURE_ERASE_INK_LUM = 110

    #: 擦五官时保留"亮且低饱和"的像素（发丝）。脸层是**实心脸形**，被后排头发
    #: 盖住的地方存的是发色；把它当五官擦掉会 inpaint 成肤色，在刘海上涂出一块
    #: 白斑（实测眼定位修好后眉毛掩码压到刘海，脸层出现 385px 白斑）。
    #: 只保留「非皮肤 + 亮度够 + 饱和度低」三种条件同时成立的像素：虹膜是亮
    #: 但高饱和（实测 0.58），睫毛/眼线是暗的，两者照常擦掉。
    FEATURE_ERASE_HAIR_SAT = 0.20

    #: SAM2 常沿绘画边界切成零交集硬接。补画必须先认出「紧贴在后排层前面的
    #: 前排层」，否则遮挡检测要求输入已经重叠，AmodalCompleter 永远不会触发。
    #: 12px 覆盖常见抗锯齿/描边宽度，又不足以把相隔一整段肢体的层误判成遮挡。
    OCCLUSION_HARD_CUT_MARGIN = 12

    #: 遮挡补画的膨胀比上限：待补像素 / 本层可见像素。
    #:
    #: 遮挡区目前取「前排层的整块掩码」，对贴身的小部件会失控：实测脖子上方
    #: 只有 583px 可见，却因为整件上衣都被算成遮挡区而补到 38,611px（66 倍），
    #: 呼吸一动就会从领口露出一大块假脖子。超过这个倍数说明「遮挡区」其实是
    #: 别的大部件，不是本层被压住的部分——此时保留原层，宁可有缝也不造假层。
    AMODAL_MAX_EXPANSION = 20.0

    #: 「擦边重叠」判据：重叠面积不到前排层自身的这个比例，就不算前排盖住了后排。
    #:
    #: 分割掩码在相邻处常互相渗 1~2px。实测 neck 与 clothes_top 只重叠 79px
    #: （占上衣 0.13%），旧逻辑却因此把整件上衣当作脖子的遮挡区（83 倍膨胀）。
    #: 真实遮挡的形状与之相反：hair_back 被 face 压住时，重叠占脸面积 4.7%。
    #: 取 2% 分界，既保住后发被脸遮挡时的整块补画，又不让小渗边触发巨型补画。
    GRAZING_OVERLAP_RATIO = 0.02

    #: 合成上眼睑：层名 -> 可以作为"眼睛可见区域"的候选层（按优先级）。
    #:
    #: 立绘里没有眼皮画稿，闭眼只能把眼球压扁——眼睛变成一坨被压扁的色块。这里合成
    #: 一张「闭眼图」：眼睛轮廓内填皮肤 + 原画自己的**上睫毛线**。
    #:
    #: 为什么不用"眼皮带下移"：带子要从眼睛上方取像素，而眼睛正上方整片是头发，
    #: 脸层在那儿存的是**发色**（不是皮肤）——照抄下来闭眼时眼睛上会留一条蓝带
    #: （实测左眼 40px 宽）。所以改成"闭眼图"：形状与颜色都在眼睛**内部**取，
    #: 绝不借用眼睛外的像素。
    EYELID_SOURCES = (
        ("eyelid_left",
         ("irides_left", "eyewhite_left", "eyes_left", "eye_left")),
        ("eyelid_right",
         ("irides_right", "eyewhite_right", "eyes_right", "eye_right")),
    )
    #: 睫毛线：眼球层里"暗且各通道接近"的像素（描边/睫毛），虹膜是蓝的会被排除。
    EYELID_LASH_LUM = 110
    EYELID_LASH_SPREAD = 30

    #: 腮红层：脸的颊部，由脸层原像素向暖粉偏。
    CHEEK_LAYER = "cheek"
    #: 腮红相对眼睛尺寸的几何。
    #:
    #: 必须**向外**偏：动漫脸两只眼睛本来就靠得近，若把椭圆正对眼睛中心放、
    #: 半径又开到一整个眼宽，两块会连成一条横跨鼻梁的硬边色带（实测 86x30 的
    #: 一条带子）。所以中心外移、半径收小，左右两块各自成团。
    CHEEK_RADIUS_X = 0.58
    CHEEK_RADIUS_Y = 0.42
    CHEEK_OUTWARD = 0.45
    CHEEK_CENTER_DY = 0.55
    #: 腮红最大不透明度（0..255）。半透明才像腮红，实心会像贴了两块色块。
    CHEEK_ALPHA = 120
    #: 腮红颜色 = 肤色 + 这个偏移（R 抬、G/B 压 -> 暖粉）。
    CHEEK_TINT = (18, -16, -12)

    #: 眼泪层：左右各一，画在眼睛下缘。静止时**不可见**，由 ParamTears × 左右淡入。
    #:
    #: 只做"下眼睑蓄泪"这一条薄新月：泪珠之类的圆点在小画布上会被网格采样成
    #: 方块（实测），反而比没有更难看。
    TEAR_SIDES = (("tear_left", 0), ("tear_right", 1))
    TEAR_RADIUS_X = 0.40
    TEAR_RADIUS_Y = 0.20
    TEAR_CENTER_DY = 0.02
    TEAR_ALPHA = 150
    TEAR_RGB = (196, 226, 255)

    #: 静止时**不可见**的合成层（靠不透明度键形淡入）。
    #:
    #: 这三层与闭眼图不同：闭眼图静止时被眼球完全挡住，而腮红/眼泪静止时必须靠
    #: **不透明度 0** 隐去。所以合成预览要跳过它们（否则静止画面凭空多出腮红），
    #: 质量门也不能把它们当成"越界扩张"（它们静止时根本不画）。
    REST_INVISIBLE_PARTS = (CHEEK_LAYER, "tear_left", "tear_right")

    #: 口内层：从嘴线层取形状与色调，合成"口腔内侧"。
    MOUTH_CAVITY_SOURCES = ("mouth", "mouth_outer", "mouth_line")
    MOUTH_CAVITY_NAME = "mouth_cavity"
    #: 口内色 = 嘴线色向暖红偏移的比例（嘴线通常是近黑的墨线）。
    MOUTH_CAVITY_WARMTH = 0.45

    @classmethod
    def synthesized_overlay_sources(cls) -> Dict[str, Tuple[str, ...]]:
        """合成层 → 它**取自**哪些层。

        闭眼图与口内都是拿源层的**同一块面积**造出来的（闭眼图 = 眼睛那块面积填
        皮肤 + 原画上睫毛；口内 = 嘴线那块面积填暖色），所以它们必然与源层
        100% 重叠。质量门按"谁盖住谁"判缺陷，不认识这条关系就会把设计报成越界
        扩张（实测 4 条：eyelid_* 各盖住 eyewhite_* 与 irides_*）。
        """
        out: Dict[str, Tuple[str, ...]] = {
            name: tuple(sources) for name, sources in cls.EYELID_SOURCES
        }
        out[cls.MOUTH_CAVITY_NAME] = tuple(cls.MOUTH_CAVITY_SOURCES)
        return out

    def __init__(self, device: str = "auto") -> None:
        """Initialize the composer.

        Args:
            device: Compute device forwarded to :class:`AmodalCompleter`.
        """
        self.amodal = AmodalCompleter(device=device)

    # ------------------------------------------------------------------ public

    def compose(
        self,
        part_masks: Dict[str, np.ndarray],
        original_image: Image.Image,
        output_dir: str,
        depth_map: Optional[np.ndarray] = None,
    ) -> Dict[str, Dict]:
        """Extract, complete, and save per-part RGBA layers.

        Args:
            part_masks: Mapping of part name -> boolean mask ``(H, W)``.
            original_image: Source character image (RGBA or convertible).
            output_dir: Directory in which to write ``<part>.png`` files.

        Returns:
            Dict keyed by part name with metadata:
            ``path``, ``bbox``, ``size``, ``pixel_count``, ``mean_color``,
            ``occluded_by``, ``completed``.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        if original_image.mode != "RGBA":
            original_image = original_image.convert("RGBA")
        img_arr = np.array(original_image)
        h, w = img_arr.shape[:2]

        source_names = {str(name) for name in part_masks}
        soft_masks = {
            str(name): np.asarray(mask)
            for name, mask in part_masks.items()
            if np.asarray(mask).dtype != np.bool_
            and np.asarray(mask).dtype.kind == "f"
        }
        part_masks, split_warnings = split_limb_parts(part_masks, (w, h))
        part_masks, eye_warnings = split_eye_parts(part_masks, img_arr)
        part_masks, hair_warnings = split_hair_parts(part_masks)
        part_masks, derived_parts = split_garment_details(part_masks, (w, h))
        # 几何细化（刘海分束 / 后侧发再分束 / 手臂分段 / 裙内层）必须排在
        # split_garment_details 之后：裙摆、腰带、袖口已经切走，剩余面积才是
        # 细化阈值的真实分母。深度前后拆分仍排在最后，作用于细化后的外层。
        part_masks, refine_warnings = refine_part_masks(part_masks, img_arr)
        depth_warnings: List[str] = []
        if depth_map is not None:
            part_masks, depth_warnings = split_depth_parts(part_masks, depth_map)
        # 拆分之后统一收拾一遍：接回被切碎的缎带、丢掉看不见的碎层。
        # 必须放在所有 split_* 之后 —— 碎片是拆分本身产生的。
        part_masks = self._stabilize_masks(part_masks, (h, w))
        all_split_notes = (split_warnings + eye_warnings + hair_warnings
                           + refine_warnings + depth_warnings)
        for message in all_split_notes:
            log.warning(message)
        # 拆分降级证据：把「不能按语义安全拆分」的原因按部件归集，随层写进
        # manifest。此前这些事实只落在日志里，清单看不到，等于把受限推断
        # 伪装成正常检测（见 staging spec 2026-11-19 INV-A1/A2）。
        split_degraded: Dict[str, List[str]] = {}
        for note in all_split_notes:
            code = getattr(note, "code", None)
            part = getattr(note, "part", None)
            if code in DEGRADATION_CODES and part:
                split_degraded.setdefault(str(part), []).append(str(note))
        if derived_parts:
            log.info(f"Derived garment layers: {derived_parts}")

        occlusion_map = self.generate_occlusion_map(part_masks)

        # 脸底层要"掏掉"的五官区域（见 FACE_PARTS 的说明）。先并起来再统一补。
        face_features = np.zeros((h, w), dtype=bool)
        for name in self.FACE_FEATURE_PARTS:
            m = part_masks.get(name)
            if m is not None:
                face_features |= np.asarray(m, dtype=bool)
        if face_features.any() and self.FEATURE_ERASE_MARGIN > 0:
            face_features = self._dilate(face_features, self.FEATURE_ERASE_MARGIN)

        layers: Dict[str, Dict] = {}
        for part, mask in part_masks.items():
            mask = np.asarray(mask, dtype=bool)
            if mask.shape != (h, w):
                log.warning(f"Skipping part '{part}': mask shape {mask.shape} != image {(h, w)}")
                continue
            if not mask.any():
                log.debug(f"Skipping empty part '{part}'")
                continue

            # Determine occluded pixels for this part: pixels that belong to
            # a part drawn in front of this one.
            occluded_by = occlusion_map.get(part, {}).get("occluded_by", [])
            occluded_mask = np.zeros((h, w), dtype=bool)
            adjacent_region = None
            for other in occluded_by:
                if other in part_masks:
                    front = np.asarray(part_masks[other], dtype=bool)
                    overlap = int((mask & front).sum())
                    # 只擦到边的重叠不算「整块盖住」：分割掩码在相邻处常互相渗
                    # 1~2px，若据此把整件前排都当遮挡区，后排层的补画会膨胀几十倍
                    # 被守卫拦下（实测 neck 因 79px 重叠被判 83 倍 → 记成缺件）。
                    grazing = overlap < max(1.0, self.GRAZING_OVERLAP_RATIO * int(front.sum()))
                    if grazing:
                        if adjacent_region is None:
                            adjacent_region = self._dilate(
                                mask, self.OCCLUSION_HARD_CUT_MARGIN)
                        front = front & adjacent_region
                    occluded_mask |= front
            occluded_mask &= ~mask  # only pixels we don't already own

            # 膨胀比守卫：见 AMODAL_MAX_EXPANSION 的说明。
            visible_px = int(mask.sum())
            completion_rejected = ""
            # 守卫只对**真的会补画**的层有意义。之前它对所有层生效，于是从不补画
            # 的层（口内、袖口…）也会被判成「补画不可信」→ 标成 manual_required
            # → 这些层被算进清单的缺件里，必需部件清单凭空多出几项，而这些层的
            # 像素其实全部来自原画、没有任何推断内容。
            attempts_completion = part in self.AMODAL_PARTS
            if (attempts_completion and visible_px
                    and int(occluded_mask.sum()) > self.AMODAL_MAX_EXPANSION * visible_px):
                log.warning(
                    f"'{part}' 的遮挡区 {int(occluded_mask.sum())}px 是可见区 "
                    f"{visible_px}px 的 "
                    f"{int(occluded_mask.sum()) / visible_px:.0f} 倍，判定为误把整个"
                    f"前排部件当成遮挡区，跳过补画（保留原层）"
                )
                completion_rejected = (
                    f"遮挡区 {int(occluded_mask.sum())}px 超过可见区 "
                    f"{self.AMODAL_MAX_EXPANSION:g} 倍，补画不可信")
                occluded_mask = np.zeros((h, w), dtype=bool)

            completed = False
            completed_pixels = 0
            completion_method: Optional[str] = None
            if part in self.AMODAL_PARTS and occluded_mask.any():
                try:
                    completed_layer = self.amodal.complete(
                        original_image,
                        visible_mask=mask,
                        occluded_mask=occluded_mask,
                    )
                    completed_arr = np.array(completed_layer)
                    # Preserve RGB from the original on visible pixels, fill
                    # alpha as union(mask, occluded_mask)
                    layer_arr = np.zeros((h, w, 4), dtype=np.uint8)
                    union = mask | occluded_mask
                    layer_arr[union, :3] = completed_arr[union, :3]
                    layer_arr[union, 3] = 255
                    completed = True
                    completed_pixels = int((union & ~mask).sum())
                    completion_method = getattr(self.amodal, "last_method", None) or "unknown"
                except Exception as exc:
                    log.warning(f"Amodal completion failed for '{part}': {exc}")
                    completion_rejected = f"amodal 补画失败: {exc}"
                    layer_arr = self._extract_layer(img_arr, mask)
            else:
                layer_arr = self._extract_layer(img_arr, soft_masks.get(part, mask))

            if part in self.FACE_PARTS and face_features.any():
                layer_arr = self._erase_features(layer_arr, mask, face_features)

            path = out / f"{part}.png"
            Image.fromarray(layer_arr, "RGBA").save(path)

            pixels = layer_arr[..., 3] > 0
            ys, xs = np.where(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else (0, 0, 0, 0)
            if pixels.any():
                mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0))
            else:
                mean_color = (0, 0, 0)

            layers[part] = {
                "name": part,
                "path": str(path),
                "bbox": bbox,
                "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
                "pixel_count": int(pixels.sum()),
                "mean_color": mean_color,
                "occluded_by": list(occluded_by),
                "completed": completed,
                # 补全证据：面积可核对；失败/被守卫拦下时该层必须人工复核。
                "completed_pixels": completed_pixels,
                "completion_rejected": completion_rejected,
                # 补画方法与基线可信度：让「这块是推断出来的」可核对、可排序。
                # 方法与置信度成对出现，缺失即契约违规（见 spec INV-A3）。
                "completion_method": completion_method,
                "completion_confidence": (
                    self.amodal.COMPLETION_CONFIDENCE.get(completion_method, 0.0)
                    if completion_method else None
                ),
                # 拆分降级证据：部件无法按语义安全拆分时，原因原文随层写出。
                "split_warnings": list(split_degraded.get(part, [])),
                "split_limited": bool(split_degraded.get(part)),
                "status": ("completed" if completed
                           else "manual_required" if completion_rejected else None),
                "provenance": ("amodal_completion" if completed
                               else "amodal_untrusted" if completion_rejected else None),
            }
            log.debug(
                f"  layer {part:14s} pixels={layers[part]['pixel_count']:>7d} "
                f"bbox={bbox} completed={completed}"
            )

        self._complete_occluded_irises(layers, img_arr, h, w)

        synthesized = self.synthesize_face_geometry(layers, h, w, out)
        if synthesized:
            log.info(f"合成表情图层（眼睑/口内）：{synthesized}")

        # 来源记账：拆分/合成出来的层不能冒充检测结果。检测层由调用方（sam2_gd
        # 的 parts_report）补 provenance/confidence；这里只负责把「相对输入新增」
        # 的层标成 derived，并给每层留下可追溯的 source_mask。
        for part, info in layers.items():
            info.setdefault("source_mask", part)
            if part in source_names and part not in synthesized:
                continue
            info.setdefault(
                "provenance",
                "synthesized" if part in synthesized else "derived_geometry",
            )
            info.setdefault("status", "derived")
            info.setdefault("derived", True)

        if depth_map is not None:
            attach_depths(layers, part_masks, depth_map)

        provenance = {name: {
            "bbox": list(info["bbox"]),
            "origin": [0, 0],
            "synthesized": bool(info.get("synthesized") or info.get("completed")),
            "alpha_checksum": hashlib.sha256(
                np.asarray(Image.open(info["path"]).convert("RGBA"))[:, :, 3].tobytes()
            ).hexdigest(),
        } for name, info in layers.items()}
        (out / "layer_provenance.json").write_text(
            json.dumps({"layers": provenance}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        log.success(f"Composed {len(layers)} layers into {out}")
        return layers

    def synthesize_face_geometry(
        self,
        layers: Dict[str, Dict],
        h: int,
        w: int,
        out: Optional[Path] = None,
    ) -> List[str]:
        """合成「闭眼图」「口内」「腮红」「眼泪」四类层，返回新增的层名。

        立绘里没有这些画稿：眼睛是睁开的、嘴是闭着的、没有腮红和眼泪。缺了它们，
        眨眼只能把眼球压扁，张嘴只能把墨线撑粗，`ParamCheek` / `ParamTears*` 更是
        改了也毫无反应。

        两种截然不同的"静止不可见"策略：
        * **被挡住**：闭眼图与口内靠更靠前的图层遮住（静止逐像素不变）。
        * **不透明度 0**：腮红与眼泪静止时是透明的（``rest_opacity=0``），由参数
          键形在运行时淡入。它们不参与"静止逐像素不变"的证明，而是靠
          ``REST_INVISIBLE_PARTS`` 与预览端跳过。

        合成失败（缺脸层/缺眼睛层/层为空）一律**不产出**，不交半层。
        """
        made: List[str] = []
        if not layers:
            return made
        dest = Path(out) if out is not None else Path(
            next(iter(layers.values()))["path"]).parent
        self._array_cache: Dict[str, Optional[np.ndarray]] = {}
        # 眼睛层合成缓存（见 _eye_solid）：同一侧眼睛在一次合成里被三个
        # 调用方各合成一遍，缓存后只算一次。生命周期与 _array_cache 一致。
        self._eye_cache: Dict[tuple, tuple] = {}
        try:
            return self._synthesize_layers(layers, h, w, dest, made)
        finally:
            self._array_cache = None
            self._eye_cache = None

    def _synthesize_layers(self, layers: Dict[str, Dict], h: int, w: int,
                           dest: Path, made: List[str]) -> List[str]:
        for name, sources in self.EYELID_SOURCES:
            if name in layers:
                continue
            present = self._draw_order([s for s in sources if s in layers])
            if not present:
                continue
            lid = self._synthesize_eyelid(name, present, layers, h, w)
            if lid is None:
                log.debug(f"跳过眼睑合成 '{name}': 眼睛层不可用")
                continue
            self._store_layer(layers, dest, name, lid)
            made.append(name)

        if self.MOUTH_CAVITY_NAME not in layers:
            present = self._draw_order(
                [s for s in self.MOUTH_CAVITY_SOURCES if s in layers])
            if present:
                cavity = self._synthesize_mouth_cavity(layers, present, h, w)
                if cavity is not None:
                    self._store_layer(layers, dest, self.MOUTH_CAVITY_NAME, cavity)
                    made.append(self.MOUTH_CAVITY_NAME)

        # ---- 静止时不可见的两类表情层（靠不透明度键形淡入）----
        # 它们**不**参与"静止逐像素不变"的证明：静止时由不透明度 0 隐去，
        # 所以 `rest_opacity=0` 必须一路带到合成预览与导出端。
        if self.CHEEK_LAYER not in layers:
            cheek = self._synthesize_cheek(layers, h, w)
            if cheek is not None:
                self._store_layer(layers, dest, self.CHEEK_LAYER, cheek,
                                  rest_opacity=0.0)
                made.append(self.CHEEK_LAYER)

        for name, index in self.TEAR_SIDES:
            if name in layers:
                continue
            tear = self._synthesize_tear(layers, h, w, index)
            if tear is not None:
                self._store_layer(layers, dest, name, tear, rest_opacity=0.0)
                made.append(name)
        return made

    def _eye_solid(self, layers: Dict[str, Dict], h: int, w: int,
                   sources) -> tuple:
        """按给定层名列表合成该侧眼睛层，返回 ``(eye, solid)``，**带缓存**。

        为什么要缓存：同一次 ``synthesize_face_geometry`` 里，同一侧眼睛会被合成
        三遍 —— ``_synthesize_tear`` 调一次 ``_eye_anchor``、``_synthesize_cheek``
        又调一次，``_synthesize_eyelid`` 自己再合成一遍。每次都是 66 万像素的
        浮点 alpha 合成，实测三次合计约 0.2 s。

        缓存键取 ``tuple(sources)`` 而不是 index：**不同调用方传的顺序不同** ——
        ``_eye_anchor`` 用 ``EYELID_SOURCES`` 的原始顺序，``_synthesize_eyelid``
        用 ``_draw_order`` 排序后的顺序，两者是**相反**的（原始是
        ``irides → eyewhite → eyes``，排序后是 ``eyes → eyewhite → irides``）。
        alpha 叠加顺序会改变结果，所以只能各缓存各的，不能混用。

        ``eye`` 与 ``solid`` 在调用方都是只读（唯一一处 ``eye[...]`` 取值），
        共享安全。
        """
        key = tuple(sources)
        cache = getattr(self, "_eye_cache", None)
        if cache is not None and key in cache:
            return cache[key]

        eye = self._alpha_over(
            [self._layer_array(layers, n) for n in sources], h, w)
        solid = None if eye is None else (eye[..., 3] == 255)
        result = (eye, solid)
        if cache is not None:
            cache[key] = result
        return result

    def _eye_anchor(self, layers: Dict[str, Dict], h: int, w: int,
                    index: int) -> Optional[tuple]:
        """该侧眼睛**不透明**像素的包围盒 ``(x0, y0, x1, y1)``；没有眼睛层则 None。"""
        _name, sources = self.EYELID_SOURCES[index]
        eye, solid = self._eye_solid(layers, h, w, sources)
        if eye is None or solid is None or not solid.any():
            return None
        ys, xs = np.nonzero(solid)
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    def _skin_array(self, layers: Dict[str, Dict]) -> Optional[np.ndarray]:
        face = self._layer_array(layers, "face")
        if face is None:
            face = self._layer_array(layers, "face_base")
        return face

    @staticmethod
    def _radial_alpha(h: int, w: int, cx: float, cy: float,
                      rx: float, ry: float, peak: float) -> np.ndarray:
        """椭圆径向衰减的 alpha（0..peak），边缘平滑到 0。"""
        if rx <= 0 or ry <= 0:
            return np.zeros((h, w), dtype=np.float64)
        ys = np.arange(h, dtype=np.float64)[:, None]
        xs = np.arange(w, dtype=np.float64)[None, :]
        norm = ((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2
        falloff = np.clip(1.0 - norm, 0.0, 1.0) ** 1.5
        return falloff * float(peak)

    def _synthesize_cheek(self, layers: Dict[str, Dict], h: int,
                          w: int) -> Optional[np.ndarray]:
        """合成腮红层：颊部两块椭圆，颜色取自脸层肤色向暖粉偏。

        静止时**不可见**（``rest_opacity=0``，由 ``ParamCheek`` 淡入），所以它不需要
        像闭眼图那样"藏在别的层后面"——但也不能是凭空画的色块：颜色来自脸层
        自己的像素，只做色偏，这样与皮肤的明暗关系保持一致。
        """
        face = self._skin_array(layers)
        if face is None:
            return None
        anchors = [(i, a) for i, a in
                   ((i, self._eye_anchor(layers, h, w, i))
                    for i in range(len(self.EYELID_SOURCES))) if a]
        if not anchors:
            return None

        skin = self._skin_color_below(face, max(a[3] for _i, a in anchors))
        if skin is None:
            return None
        tint = np.asarray(self.CHEEK_TINT, dtype=np.int16)
        colour = np.clip(skin.astype(np.int16) + tint, 0, 255).astype(np.uint8)

        alpha = np.zeros((h, w), dtype=np.float64)
        for index, (x0, y0, x1, y1) in anchors:
            eye_w = max(1.0, float(x1 - x0))
            eye_h = max(1.0, float(y1 - y0))
            # 向外偏（左眼往左、右眼往右），两团不连成一条横带
            sign = -1.0 if index == 0 else 1.0
            if len(anchors) == 1:
                sign = 0.0
            alpha = np.maximum(alpha, self._radial_alpha(
                h, w,
                cx=(x0 + x1) / 2.0 + sign * self.CHEEK_OUTWARD * eye_w,
                cy=y1 + self.CHEEK_CENTER_DY * eye_h,
                rx=self.CHEEK_RADIUS_X * eye_w,
                ry=self.CHEEK_RADIUS_Y * eye_h,
                peak=self.CHEEK_ALPHA))

        # 只画在脸的**实际皮肤**上：脸层在刘海/五官处是镂空的，越界会糊到头发上。
        on_face = face[..., 3] > 0
        alpha = np.where(on_face, alpha, 0.0)
        if not (alpha > 0).any():
            return None

        cheek = np.zeros((h, w, 4), dtype=np.uint8)
        cheek[..., :3] = colour
        cheek[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        return cheek

    def _synthesize_tear(self, layers: Dict[str, Dict], h: int, w: int,
                         index: int) -> Optional[np.ndarray]:
        """合成单侧眼泪层：眼睛下缘的一小片高光，外加一颗泪珠。

        静止时**不可见**（``rest_opacity=0``，由 ``ParamTears × ParamTearsL/R``
        淡入）。画在眼睛**之上**（见 ``STANDARD_LAYER_ORDER``），所以只覆盖下眼睑
        附近，不去遮瞳孔。
        """
        anchor = self._eye_anchor(layers, h, w, index)
        if anchor is None:
            return None
        face = self._skin_array(layers)
        x0, y0, x1, y1 = anchor
        eye_w = max(1.0, float(x1 - x0))
        eye_h = max(1.0, float(y1 - y0))
        cx = (x0 + x1) / 2.0
        cy = y1 + self.TEAR_CENTER_DY * eye_h

        alpha = self._radial_alpha(
            h, w, cx=cx, cy=cy,
            rx=self.TEAR_RADIUS_X * eye_w,
            ry=self.TEAR_RADIUS_Y * eye_h,
            peak=self.TEAR_ALPHA)

        if face is not None:
            # 眼泪可以压在眼球上，但不能糊到脸外的头发/背景上
            visible = face[..., 3] > 0
            eye_own = self._alpha_over(
                [self._layer_array(layers, n)
                 for n in self.EYELID_SOURCES[index][1]], h, w)
            if eye_own is not None:
                visible = visible | (eye_own[..., 3] > 0)
            alpha = np.where(visible, alpha, 0.0)
        if not (alpha > 0).any():
            return None

        tear = np.zeros((h, w, 4), dtype=np.uint8)
        tear[..., :3] = np.asarray(self.TEAR_RGB, dtype=np.uint8)
        tear[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        return tear

    def _synthesize_eyelid(self, name: str, sources: List[str],
                           layers: Dict[str, Dict], h: int,
                           w: int) -> Optional[np.ndarray]:
        """合成「闭眼图」，画在眼球**下面**（见 ``STANDARD_LAYER_ORDER``）。

        闭眼时眼球整体**向上收拢**进上睫毛里（``eye`` profile，轴心取眼睛轮廓上沿），
        收拢后露出来的就是这张图。

        闭眼图 = 眼睛轮廓内填皮肤 + **原画自己的上睫毛线**（暗、且各通道接近的
        像素；虹膜是蓝的，各通道差大，会被排除）。

        为什么不能"从眼睛上方搬一条眼皮带下来"：眼睛正上方整片是头发，脸层在那儿
        存的是**发色**（原图像素），照抄下来闭眼时眼睛上会留一条 40px 宽的蓝带
        （实测）。闭眼图的像素全部取自眼睛**内部**，不借用眼睛外的像素。

        静止姿态逐像素不变：
        * 皮肤部分 = 脸层自己的像素（该区域已被 inpaint 成肤色）；
        * 睫毛部分 = 眼球层自己的像素，叠在眼球层上完全一样。
        """
        eye, solid = self._eye_solid(layers, h, w, sources)
        if eye is None or solid is None or not solid.any():
            return None

        face = self._layer_array(layers, "face")
        if face is None:
            face = self._layer_array(layers, "face_base")
        if face is None:
            return None

        ys, xs = np.nonzero(solid)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        # 上睫毛线：眼球层里暗、且各通道接近的像素。只取眼睛**上半部** ——
        # 下半部的深色是下睫毛/眼眶线，闭眼时不该留在原位。
        rgb = eye[..., :3].astype(np.int16)
        lum = rgb.mean(axis=2)
        spread = rgb.max(axis=2) - rgb.min(axis=2)
        dark = solid & (lum < self.EYELID_LASH_LUM) & (spread < self.EYELID_LASH_SPREAD)
        upper = np.zeros_like(dark)
        upper[y0:(y0 + y1) // 2 + 1, :] = True
        lash = dark & upper
        if not lash.any():
            return None

        # 皮肤：脸层在眼区已被 inpaint 成肤色，直接搬过来；脸层透明的地方
        # （眼区在脸层是有洞的）用眼睛下方脸颊的肤色补上。
        skin = self._skin_color_below(face, y1)
        if skin is None:
            return None
        region_face = face[y0:y1 + 1, x0:x1 + 1]
        body = region_face[..., :3].astype(np.int16).copy()
        hole = region_face[..., 3] != 255
        body[hole] = skin.astype(np.int16)

        lid = np.zeros((h, w, 4), dtype=np.uint8)
        region = lid[y0:y1 + 1, x0:x1 + 1]
        region[..., :3] = np.clip(body, 0, 255).astype(np.uint8)
        region[..., 3] = np.where(solid[y0:y1 + 1, x0:x1 + 1], 255, 0)
        # 睫毛线：原样保留眼球层的像素（静止时与眼球层逐字节相同）。
        local_lash = lash[y0:y1 + 1, x0:x1 + 1]
        region[local_lash] = eye[y0:y1 + 1, x0:x1 + 1][local_lash]
        return lid

    def _skin_color_below(self, face: Optional[np.ndarray], eye_bottom: int
                           ) -> Optional[np.ndarray]:
        """眼睛下方脸颊的肤色中位数（闭眼图里补脸层破洞用）。"""
        if face is None:
            return None
        below = face[eye_bottom + 1:, :, :3]
        alpha = face[eye_bottom + 1:, :, 3]
        keep = alpha == 255
        if not keep.any():
            return None
        rgb = below[keep].astype(np.int16)
        lum = rgb.mean(axis=1)
        spread = rgb.max(axis=1) - rgb.min(axis=1)
        skinish = (lum >= 140) & (spread <= 90)
        pool = rgb[skinish] if skinish.any() else rgb
        return np.median(pool, axis=0).astype(np.uint8)

    def _synthesize_mouth_cavity(self, layers: Dict[str, Dict],
                                 sources: List[str], h: int,
                                 w: int) -> Optional[np.ndarray]:
        """按嘴唇**内侧**生成口内层：几何比嘴唇小一圈，静止时被嘴唇盖住。

        旧实现直接把嘴唇整块 alpha 复制过去，导致 mouth_cavity 与 mouth 的
        bbox、像素数完全相同（实测 161×134 / 13,129 两者一致）——分层列表里
        出现两条同尺寸图层，张开时整片嘴唇都会变成口腔色。改为内缩区域；
        缩不出足够面积就不生成，宁可没有口内层，也不留一层的复制品。
        """
        mouth = self._alpha_over([self._layer_array(layers, n) for n in sources], h, w)
        if mouth is None:
            return None
        mask = mouth[..., 3] >= 128
        area = int(mask.sum())
        if area < 12:
            return None
        interior = self._erode_mask(mask, 2)
        if interior.sum() < max(8, int(area * 0.12)):
            interior = self._erode_mask(mask, 1)
        if interior.sum() < max(8, int(area * 0.08)):
            # 嘴太细（闭口的一条线）时没有可信的「内部」，不生成重复层。
            return None
        line = np.median(mouth[mask][:, :3], axis=0).astype(np.float32)
        # 墨线多为近黑：往暖红抬，得到"口腔内侧"而不是第二条黑线。
        warm = np.array([140.0, 56.0, 56.0], dtype=np.float32)
        ratio = min(1.0, max(0.0, float(self.MOUTH_CAVITY_WARMTH)))
        colour = np.clip(line * (1.0 - ratio) + warm * ratio, 0, 255).astype(np.uint8)
        cavity = np.zeros((h, w, 4), dtype=np.uint8)
        cavity[interior, :3] = colour
        cavity[interior, 3] = 255
        return cavity

    @staticmethod
    def _erode_mask(mask: np.ndarray, radius: int = 1) -> np.ndarray:
        """按 4 邻域做二值腐蚀（纯 numpy，避免为了 1px 内缩引入 opencv 依赖）。"""
        out = mask.astype(bool)
        for _ in range(max(0, int(radius))):
            nxt = out.copy()
            nxt[1:, :] &= out[:-1, :]
            nxt[:-1, :] &= out[1:, :]
            nxt[:, 1:] &= out[:, :-1]
            nxt[:, :-1] &= out[:, 1:]
            out = nxt
        return out

    def _draw_order(self, names: List[str]) -> List[str]:
        """按 ``STANDARD_LAYER_ORDER`` 排序（越靠后 = 越靠前）。"""
        rank = {n: i for i, n in enumerate(self.STANDARD_LAYER_ORDER)}
        return sorted(names, key=lambda n: rank.get(n, len(rank)))

    def _alpha_over(self, stack, h: int, w: int) -> Optional[np.ndarray]:
        """按绘制顺序把若干层叠成一张（None 项跳过）。

        首选 Rust 内核（native/split_kernel，与本函数 float64 公式逐字节
        对拍，见 tests/unit/test_native_kernel.py）；DLL 缺失或关闭时
        走下面的原实现，行为完全一致。
        """
        from core import native_kernel

        native = native_kernel.alpha_over(stack, h, w)
        if native is not None:
            return native
        arrays = [a for a in stack if a is not None]
        if not arrays:
            return None
        canvas = np.zeros((h, w, 4), dtype=np.float64)
        for src in arrays:
            alpha = src[..., 3:4].astype(np.float64) / 255.0
            canvas[..., :3] = src[..., :3] * alpha + canvas[..., :3] * (1.0 - alpha)
            canvas[..., 3:4] = alpha * 255.0 + canvas[..., 3:4] * (1.0 - alpha)
        return np.clip(canvas, 0, 255).astype(np.uint8)

    def _layer_array(self, layers: Dict[str, Dict], key: str) -> Optional[np.ndarray]:
        info = layers.get(key)
        if not isinstance(info, dict):
            return None
        path = info.get("path")
        if not path:
            return None
        cache = getattr(self, "_array_cache", None)
        if cache is not None and path in cache:
            return cache[path]
        arr = self._load_png(path)
        if cache is not None:
            cache[path] = arr
        return arr

    @staticmethod
    def _load_png(path) -> Optional[np.ndarray]:
        if not path or not Path(path).is_file():
            return None
        return np.array(Image.open(path).convert("RGBA"))

    #: 虹膜「核心」高宽比低于此值，说明被刘海/睫毛遮掉了一大半。
    #: 注意用的是原图高饱和像素的 bbox，而不是图层 bbox：图层 bbox 里混着
    #: 睫毛/眼线，宽高等于整个眼窝（实测 64×61），拿它当虹膜尺寸会把补全
    #: 画成一整块盖住眼睛和刘海的大圆盘。
    IRIS_MIN_ASPECT = 0.55
    #: 核心像素的判定下限：饱和度与亮度。都取"虹膜本体"而不是睫毛/皮肤。
    IRIS_CORE_SAT = 0.30
    IRIS_CORE_BRIGHT = 80
    #: 一侧至少要有这么多核心像素才认为它给出了可信的虹膜尺寸；
    #: 不足则不补（宁可有缝，也不要凭 10 个像素造一块假虹膜）。
    IRIS_MIN_CORE_PIXELS = 40

    def _complete_occluded_irises(self, layers: Dict[str, Dict], img_arr: np.ndarray,
                                  h: int, w: int) -> None:
        """被遮挡的虹膜按「对侧虹膜尺寸 + 虹膜饱和色」补成完整椭圆。

        现场：刘海压住右眼，``irides_right`` 只剩 50×15 的一条，左眼是
        64×61。Live2D 里虹膜应在刘海层之下完整存在，缺一块会让眨眼/闭眼时
        右侧出现空洞。

        几何只信**原图里的高饱和虹膜像素**：一侧被遮住时，未遮挡的对侧给出
        真实虹膜尺寸，本侧可见的那条给出水平中心与底边（虹膜坐在下眼睑上）。
        补出来的椭圆因此落在真实虹膜大小内，不会盖到眼白/刘海。
        """
        sides = ("left", "right")
        for side in sides:
            if not isinstance(layers.get(f"irides_{side}"), dict):
                return  # 缺一侧就不补：没有参照比例，补出来只会更假

        boxes = {s: tuple(layers[f"irides_{s}"]["bbox"]) for s in sides}
        cores = {s: self._iris_core(img_arr, boxes[s]) for s in sides}
        if any(core is None for core in cores.values()):
            # 原图里认不出虹膜配色（灰瞳/强滤镜/坐标错位）时不补：
            # 没有可信参照，补出来的只会是凭空一块色块。
            log.debug(
                "跳过虹膜补全：虹膜核心像素不足 "
                f"{sorted(s for s, c in cores.items() if c is None)}"
            )
            return

        ref_side = max(sides, key=lambda s: cores[s]["bbox"][3] - cores[s]["bbox"][1])
        ref = cores[ref_side]["bbox"]
        ref_w = ref[2] - ref[0] + 1
        ref_h = ref[3] - ref[1] + 1
        iris_colour = np.median(
            np.concatenate([c["pixels"] for c in cores.values()]), axis=0
        ).astype(np.uint8)

        yy, xx = np.mgrid[0:h, 0:w]
        for side in sides:
            own = cores[side]["bbox"]
            own_w, own_h = own[2] - own[0] + 1, own[3] - own[1] + 1
            if own_h / max(1, own_w) >= self.IRIS_MIN_ASPECT:
                continue  # 这一侧本来就完整
            if own_h >= ref_h * 0.6:
                # 缺口不够大就不补。**透视**是这里的现实风险：3/4 侧脸时远侧
                # 眼睛本来就比近侧小（实测右眼 508×199~536×219 vs 左眼
                # 437×174~500×236），按对侧尺寸"补全"会把它放大成一只假眼。
                continue

            info = layers[f"irides_{side}"]
            radius_x = ref_w / 2.0
            radius_y = ref_h / 2.0
            cx = (own[0] + own[2]) / 2.0
            cy = own[3] - radius_y  # 底边对齐可见核心底边（坐在下眼睑上）
            ellipse = ((xx - cx) / max(radius_x, 1.0)) ** 2 + (
                (yy - cy) / max(radius_y, 1.0)
            ) ** 2 <= 1.0

            arr = np.array(Image.open(info["path"]).convert("RGBA"))
            visible = arr[..., 3] > 0
            fill = ellipse & ~visible
            if int(fill.sum()) < 20:
                continue
            arr[fill, :3] = iris_colour
            arr[fill, 3] = 255
            Image.fromarray(arr, "RGBA").save(info["path"])

            pixels = arr[..., 3] > 0
            ys, xs = np.where(pixels)
            info["bbox"] = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            info["size"] = (
                int(info["bbox"][2] - info["bbox"][0] + 1),
                int(info["bbox"][3] - info["bbox"][1] + 1),
            )
            info["pixel_count"] = int(pixels.sum())
            info["mean_color"] = tuple(
                int(c) for c in arr[pixels][:, :3].mean(axis=0)
            )
            log.info(
                f"虹膜补全：irides_{side} 可见 {own_w}×{own_h} → 补齐 "
                f"{int(fill.sum())} 像素（{info['size'][0]}×{info['size'][1]}）"
            )

    def _iris_core(self, img_arr: np.ndarray, box: tuple) -> Optional[dict]:
        """在原图的一只眼眶里取出「高饱和虹膜像素」的 bbox 与像素池。

        返回 ``{"bbox": (x0, y0, x1, y1), "pixels": (N, 3) float32}``；
        像素太少（低于 ``IRIS_MIN_CORE_PIXELS``）时返回 ``None``，表示这一侧
        没有可信的虹膜参照。
        """
        rgb = np.asarray(img_arr)
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            return None
        rgb = rgb[..., :3]
        height, width = rgb.shape[:2]
        x0, y0, x1, y1 = (int(v) for v in box)
        x0, x1 = max(0, min(x0, width - 1)), max(0, min(x1, width - 1))
        y0, y1 = max(0, min(y0, height - 1)), max(0, min(y1, height - 1))
        patch = rgb[y0 : y1 + 1, x0 : x1 + 1].reshape(-1, 3).astype(np.float32)
        if patch.size == 0:
            return None

        hi = patch.max(axis=1)
        lo = patch.min(axis=1)
        sat = (hi - lo) / np.clip(hi, 1.0, None)
        keep = (sat >= self.IRIS_CORE_SAT) & (hi >= self.IRIS_CORE_BRIGHT)
        if int(keep.sum()) < self.IRIS_MIN_CORE_PIXELS:
            return None

        local_h, local_w = y1 - y0 + 1, x1 - x0 + 1
        mask = keep.reshape(local_h, local_w)
        ys, xs = np.where(mask)
        bbox = (
            x0 + int(xs.min()), y0 + int(ys.min()),
            x0 + int(xs.max()), y0 + int(ys.max()),
        )
        return {"bbox": bbox, "pixels": patch[keep]}

    def _store_layer(self, layers: Dict[str, Dict], dest: Path, name: str,
                     layer_arr: np.ndarray, rest_opacity: float = 1.0) -> Dict:
        """落盘一张合成层并登记与普通层同构的元数据。

        ``rest_opacity`` 是**静止姿态下的不透明度**：腮红/眼泪这类"淡入"表情层
        静止时为 0（不可见），由参数键形在运行时抬到 1。
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{name}.png"
        Image.fromarray(layer_arr, "RGBA").save(path)
        pixels = layer_arr[..., 3] > 0
        if pixels.any():
            ys, xs = np.nonzero(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0))
        else:
            bbox = (0, 0, 0, 0)
            mean_color = (0, 0, 0)
        layers[name] = {
            "name": name,
            "path": str(path),
            "bbox": bbox,
            "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
            "pixel_count": int(pixels.sum()),
            "mean_color": mean_color,
            "occluded_by": [],
            "completed": False,
            "synthesized": True,
            "rest_opacity": float(rest_opacity),
        }
        log.debug(f"  layer {name:14s} pixels={layers[name]['pixel_count']:>7d} "
                  f"bbox={bbox} synthesized=True rest_opacity={rest_opacity}")
        return layers[name]

    def reorder_layers(
        self,
        layers: Dict[str, Dict],
        target_order: Optional[List[str]] = None,
    ) -> Dict[str, Dict]:
        """Return an ordered dict of layers keyed by the target draw order.

        Layers whose names do not appear in ``target_order`` are appended at
        the end in alphabetical order so no layer is lost.

        Args:
            layers: Dict of part name -> layer metadata.
            target_order: Desired order. Defaults to ``STANDARD_LAYER_ORDER``.

        Returns:
            New dict with keys ordered back-to-front.
        """
        from collections import OrderedDict

        order = list(target_order) if target_order is not None else list(self.STANDARD_LAYER_ORDER)
        ordered: "OrderedDict[str, Dict]" = OrderedDict()
        seen = set()
        for name in order:
            if name in layers:
                ordered[name] = layers[name]
                seen.add(name)
        for name in sorted(k for k in layers if k not in seen):
            ordered[name] = layers[name]
        return ordered

    def generate_layer_json(
        self,
        layers: Dict[str, Dict],
        output_path: str,
    ) -> str:
        """Serialize layer metadata to JSON.

        Args:
            layers: Dict of part name -> metadata (as from :meth:`compose`).
            output_path: Destination JSON path.

        Returns:
            Absolute path of the written JSON file.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ordered = self.reorder_layers(layers)
        payload = {
            "standard_order": self.STANDARD_LAYER_ORDER,
            "layer_count": len(ordered),
            "layers": [
                {
                    "name": info["name"],
                    "draw_index": idx,
                    "path": info["path"],
                    "bbox": list(info["bbox"]),
                    "size": list(info["size"]),
                    "pixel_count": info["pixel_count"],
                    "mean_color": list(info["mean_color"]),
                    "occluded_by": info.get("occluded_by", []),
                    "amodal_completed": bool(info.get("completed", False)),
                    "rest_opacity": float(info.get("rest_opacity", 1.0)),
                }
                for idx, info in enumerate(ordered.values())
            ],
        }
        with out.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info(f"Wrote layer JSON -> {out}")
        return str(out.resolve())

    def generate_occlusion_map(
        self,
        part_masks: Dict[str, np.ndarray],
    ) -> Dict[str, Dict[str, List[str]]]:
        """For each layer, list layers it occludes and that occlude it.

        A layer ``B`` is considered to occlude layer ``A`` when ``B`` is
        drawn after ``A`` (appears later in :attr:`STANDARD_LAYER_ORDER`) and
        their masks intersect.

        Args:
            part_masks: part name -> boolean mask.

        Returns:
            ``{part: {"occludes": [...], "occluded_by": [...]}}``
        """
        order = self.STANDARD_LAYER_ORDER
        # Assign draw indices. Unknown parts sort after known ones alphabetically.
        names = list(part_masks.keys())
        known = [n for n in order if n in part_masks]
        unknown = sorted(n for n in names if n not in order)
        draw_seq = known + unknown

        # Pre-bool all masks
        masks = {n: np.asarray(part_masks[n], dtype=bool) for n in names}

        # 每层的「非空」与「包围盒」只算一次。
        #
        # 原实现在两两循环里对每一对都调 _bbox_overlap，而它内部是
        # np.where(mask) —— 一次全画布（本例 667×1000 ≈ 66 万像素）扫描。
        # 35 层共 ~595 对、每对 2 次，等于 ~1190 次全图扫描；而包围盒
        # 与层内容无关、只跟掩码本身有关，算 35 次就够了。
        # 实测这一项占了 occlusion map 生成时间的大头（1.10s）。
        nonempty: Dict[str, bool] = {}
        bboxes: Dict[str, Tuple[int, int, int, int]] = {}
        probe_masks: Dict[str, np.ndarray] = {}
        probe_bboxes: Dict[str, Tuple[int, int, int, int]] = {}
        for n in names:
            ys, xs = np.nonzero(masks[n])
            nonempty[n] = len(xs) > 0
            if nonempty[n]:
                bboxes[n] = (int(xs.min()), int(ys.min()),
                             int(xs.max()), int(ys.max()))
            else:
                bboxes[n] = (0, 0, 0, 0)
            # 只有会被有意补大的后排部件才做扩边；前排层保持原掩码，避免把
            # 「相邻」扩大成「大面积互相遮挡」。
            probe_masks[n] = (self._dilate(masks[n], self.OCCLUSION_HARD_CUT_MARGIN)
                              if n in self.AMODAL_PARTS and nonempty[n]
                              else masks[n])
            ys, xs = np.nonzero(probe_masks[n])
            probe_bboxes[n] = ((int(xs.min()), int(ys.min()),
                               int(xs.max()), int(ys.max()))
                               if len(xs) else (0, 0, 0, 0))

        result: Dict[str, Dict[str, List[str]]] = {}
        for name in names:
            result[name] = {"occludes": [], "occluded_by": []}

        for i, a in enumerate(draw_seq):
            if not nonempty[a]:
                continue
            ma = masks[a]
            ax0, ay0, ax1, ay1 = probe_bboxes[a]
            for b in draw_seq[i + 1:]:
                if not nonempty[b]:
                    continue
                bx0, by0, bx1, by1 = bboxes[b]
                # Fast pre-check via precomputed bounding boxes
                if ax0 > bx1 or bx0 > ax1 or ay0 > by1 or by0 > ay1:
                    continue
                mb = masks[b]
                # 真实交集是传统判据；amodal 层的扩边交集专门接住硬切边界。
                if np.any(ma & mb) or np.any(probe_masks[a] & mb):
                    # b is drawn in front of a -> b occludes a
                    result[a]["occluded_by"].append(b)
                    result[b]["occludes"].append(a)
        return result

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _extract_layer(img_arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Return an RGBA layer, accepting boolean or normalized soft alpha masks."""
        weights = np.asarray(mask)
        out = np.zeros(img_arr.shape, dtype=np.uint8)
        if weights.dtype == np.bool_:
            out[weights] = img_arr[weights]
            return out
        if weights.shape != img_arr.shape[:2]:
            raise ValueError("mask shape must match image")
        scale = 255.0 if weights.size and float(np.nanmax(weights)) <= 1.0 else 1.0
        alpha = np.clip(np.rint(weights.astype(np.float64) * scale), 0, 255).astype(np.uint8)
        out[..., :3] = img_arr[..., :3]
        out[..., 3] = np.minimum(img_arr[..., 3], alpha)
        return out

    @staticmethod
    def partition_alpha(img_arr: np.ndarray, first: np.ndarray,
                        second: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        first_alpha = np.clip(np.rint(np.asarray(first, dtype=np.float64) * 255), 0, 255).astype(np.uint8)
        second_alpha = 255 - first_alpha
        outputs = []
        for alpha in (first_alpha, second_alpha):
            layer = np.zeros_like(img_arr, dtype=np.uint8)
            layer[..., :3] = img_arr[..., :3]
            layer[..., 3] = alpha
            outputs.append(layer)
        return outputs[0], outputs[1]

    @staticmethod
    def composite_partition(layers: Tuple[np.ndarray, ...]) -> np.ndarray:
        if not layers:
            raise ValueError("layers must not be empty")
        rgba = np.zeros_like(layers[0], dtype=np.float64)
        for layer in layers:
            alpha = layer[..., 3:4].astype(np.float64) / 255.0
            previous_alpha = rgba[..., 3:4] / 255.0
            combined_alpha = alpha + previous_alpha
            numerator = (layer[..., :3] * alpha
                         + rgba[..., :3] * previous_alpha)
            rgba[..., :3] = np.divide(
                numerator, combined_alpha,
                out=np.zeros_like(numerator), where=combined_alpha > 0,
            )
            rgba[..., 3:4] = combined_alpha * 255
        return np.clip(np.rint(rgba), 0, 255).astype(np.uint8)

    @staticmethod
    def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
        """布尔掩码膨胀 ``radius`` 像素（无 cv2 时退化为原样返回）。"""
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            return mask
        size = max(1, int(radius) * 2 + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)

    @staticmethod
    def _close(mask: np.ndarray, radius: int) -> np.ndarray:
        """闭运算：先膨胀后腐蚀，把 ≤2px 的缝接起来而不改变外轮廓大小。"""
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            return mask
        size = max(1, int(radius) * 2 + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        return closed.astype(bool)

    @staticmethod
    def _drop_dust(mask: np.ndarray, min_px: int) -> np.ndarray:
        """丢掉面积小于 ``min_px`` 的孤立连通块。"""
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            return mask
        num, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8)
        keep = np.zeros(mask.shape, dtype=bool)
        for idx in range(1, num):
            if stats[idx, cv2.CC_STAT_AREA] >= min_px:
                keep |= labels == idx
        return keep

    def _stabilize_masks(
        self,
        masks: Dict[str, np.ndarray],
        size: Tuple[int, int],
    ) -> Dict[str, np.ndarray]:
        """接合碎片、剔除碎层与看不见的层。

        两个实测问题都出在这一步之前：
        * SAM 把一条缎带切成多块（相距 1~2px），导出后像被撕开；
        * ``split_garment_details`` 会留下 74px 的 ``skirt_hem`` 这种空壳层。

        五官层豁免：鼻子只有十几个像素、睫毛是细线，任何"最小面积"规则都会
        把它们误杀，闭运算也会把睫毛粘成一坨。
        """
        h, w = size
        area_floor = int(self.MIN_LAYER_AREA_RATIO * h * w)
        # 进入缝合/剪枝之前的原始掩码：用来区分「原画本来就有重叠」和「本步骤
        # 新长出来的重叠」（见 _drop_growth_into_front 的说明）。
        originals: Dict[str, np.ndarray] = {
            name: np.asarray(mask, dtype=bool).copy()
            for name, mask in masks.items()
            if np.asarray(mask).shape == (h, w)
        }
        kept: Dict[str, np.ndarray] = {}
        dropped: List[str] = []
        bridged: List[str] = []
        skirt_parts = {
            "clothes_bottom", "skirt", "skirt_body", "skirt_front",
            "skirt_back", "bottomwear", "bottomwear_front", "bottomwear_back",
            "skirt_inner", "skirt_hem", "skirt_hem_L", "skirt_hem_R",
            "waistband",
        }
        skirt_original: Dict[str, np.ndarray] = {}
        for name, mask in masks.items():
            if name in skirt_parts:
                source = np.asarray(mask, dtype=bool)
                if source.shape == (h, w):
                    skirt_original[name] = source.copy()
        for part, mask in masks.items():
            m = np.asarray(mask, dtype=bool)
            if m.shape != (h, w) or not m.any():
                dropped.append(part)
                continue
            if part in self.FACE_FEATURE_PARTS or part in self.RIG_REQUIRED_PARTS:
                kept[part] = m
                continue
            if part in skirt_parts:
                if int(m.sum()) < area_floor:
                    dropped.append(part)
                    continue
                kept[part] = m
                continue
            if self.MASK_CLOSE_RADIUS > 0:
                closed = self._close(m, self.MASK_CLOSE_RADIUS)
                if int(closed.sum()) > int(m.sum()):
                    bridged.append(part)
                m = closed
            total = int(m.sum())
            if total <= 0:
                dropped.append(part)
                continue
            m = self._drop_dust(m, max(self.DUST_MIN_PX, int(self.DUST_AREA_RATIO * total)))
            if int(m.sum()) < area_floor:
                dropped.append(part)
                continue
            kept[part] = m
        if skirt_original:
            original_union = np.logical_or.reduce(list(skirt_original.values()))
            kept_skirt = [mask for name, mask in kept.items() if name in skirt_parts]
            source_union = np.logical_or.reduce(kept_skirt) if kept_skirt else np.zeros((h, w), dtype=bool)
            missing = original_union & ~source_union
            if missing.any():
                for part in ("clothes_bottom", "skirt", "skirt_body", "skirt_front", "bottomwear"):
                    source = skirt_original.get(part)
                    if part in kept and source is not None:
                        kept[part] |= source & missing
                        break
        if bridged:
            log.info(f"Bridged split fragments: {sorted(bridged)}")
        if dropped:
            log.info(f"Dropped empty/negligible layers (<{area_floor}px): {sorted(dropped)}")
        return self._drop_growth_into_front(kept, originals, (h, w))

    def _drop_growth_into_front(
        self,
        kept: Dict[str, np.ndarray],
        originals: Dict[str, np.ndarray],
        size: Tuple[int, int],
    ) -> Dict[str, np.ndarray]:
        """缝合/闭运算不得把后排层长进它前面的层。

        闭运算会把细缝桥接起来，副作用是让后排层向外长 1~2px。若这几像素原本
        属于**画在它前面**的层，就凭空造出了一处重叠；而遮挡图把重叠解读成
        「后面的层被整块前排挡住」，于是脖子被判定遮挡区 76 倍、补画被守卫
        拦下，最终在清单里记成缺件（实测真实立绘：缝合前 neck 838px 与上衣
        零重叠，缝合后 960px、重叠 152px）。

        这里只退回**本步骤新长出来的**像素：原画本来就存在的重叠保留原样，
        因为那是「谁在谁后面」的真实证据（后发被脸压住正是靠它触发补画）。
        """
        h, w = size
        order = self.STANDARD_LAYER_ORDER
        known = [n for n in order if n in kept]
        unknown = sorted(n for n in kept if n not in order)
        draw_seq = known + unknown

        front_owned = np.zeros((h, w), dtype=bool)
        result: Dict[str, np.ndarray] = {}
        for name in reversed(draw_seq):
            current = np.asarray(kept[name], dtype=bool)
            original = originals.get(name)
            if original is not None:
                grown = current & ~original
                if grown.any() and front_owned.any():
                    current = current & ~(grown & front_owned)
                front_owned |= original
            else:
                front_owned |= current
            if current.any():
                result[name] = current
        result = {name: result[name] for name in kept if name in result}
        return result

    @staticmethod
    def _erase_features(layer_arr: np.ndarray, mask: np.ndarray,
                        feature_mask: np.ndarray) -> np.ndarray:
        """把脸层里被五官盖住的像素换成周围肤色（alpha 不变）。

        为什么不直接把五官区域挖成透明：眨眼/闭嘴时会把脸上的洞露出来，透出背景。
        这里用「脸层自身可见像素」作为唯一已知区域做 inpaint，填充色因此只会来自
        额头/脸颊的皮肤，不会把头发或背景的颜色渗进来。

        只擦「皮肤 / 深色墨线 / 高饱和虹膜」，**亮而低饱和的发丝原样保留**：
        脸层是实心脸形，刘海被画在脸层后面时，那一块存的就是发色；不加这条
        约束会把刘海 inpaint 成肤色，在额头上涂出一块白斑。
        """
        holes = np.asarray(mask, dtype=bool) & np.asarray(feature_mask, dtype=bool)
        if not holes.any():
            return layer_arr
        rgb = layer_arr[..., :3]
        lum = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
               + 0.114 * rgb[..., 2]).astype(np.float32)
        hi = rgb.max(axis=2).astype(np.float32)
        lo = rgb.min(axis=2).astype(np.float32)
        sat = (hi - lo) / np.clip(hi, 1.0, None)
        hair_like = (
            (lum >= LayerComposer.FEATURE_ERASE_INK_LUM)
            & (sat < LayerComposer.FEATURE_ERASE_HAIR_SAT)
            & ~face_detect.skin_mask(rgb)
        )
        holes &= ~hair_like
        if not holes.any():
            return layer_arr
        known = np.asarray(mask, dtype=bool) & ~holes
        if not known.any():
            return layer_arr
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            fill = np.median(layer_arr[known][:, :3], axis=0).astype(np.uint8)
            layer_arr[holes, :3] = fill
            return layer_arr

        ys, xs = np.nonzero(mask)
        pad = 8
        h, w = mask.shape
        y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
        x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
        sub_src = np.ascontiguousarray(layer_arr[y0:y1, x0:x1, :3])
        # 「脸层可见像素」之外的区域也标成待补：否则 inpaint 会把画布上的黑色当已知色
        unknown = (~known[y0:y1, x0:x1]).astype(np.uint8) * 255
        bgr = cv2.cvtColor(sub_src, cv2.COLOR_RGB2BGR)
        filled = cv2.cvtColor(
            cv2.inpaint(bgr, unknown, 5, cv2.INPAINT_TELEA), cv2.COLOR_BGR2RGB)
        sub_holes = holes[y0:y1, x0:x1]
        view = layer_arr[y0:y1, x0:x1]
        view[sub_holes, :3] = filled[sub_holes]
        return layer_arr

    @staticmethod
    def _bbox_overlap(a: np.ndarray, b: np.ndarray) -> bool:
        """Return True if boolean masks *might* overlap (cheap bbox test)."""
        def bbox(m: np.ndarray):
            ys, xs = np.where(m)
            if len(xs) == 0:
                return (0, 0, 0, 0)
            return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        ax0, ay0, ax1, ay1 = bbox(a)
        bx0, by0, bx1, by1 = bbox(b)
        if ax0 > bx1 or bx0 > ax1 or ay0 > by1 or by0 > ay1:
            return False
        return True
