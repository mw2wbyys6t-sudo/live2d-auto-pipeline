#include "models.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>

// ─────────────────── 图像工具 ───────────────────

void bilinear_resize_rgb(const uint8_t* src, int sw, int sh,
                         uint8_t* dst, int dw, int dh) {
    const float x_ratio = static_cast<float>(sw) / dw;
    const float y_ratio = static_cast<float>(sh) / dh;
    for (int dy = 0; dy < dh; ++dy) {
        for (int dx = 0; dx < dw; ++dx) {
            float sx = dx * x_ratio, sy = dy * y_ratio;
            int x0 = static_cast<int>(std::floor(sx));
            int y0 = static_cast<int>(std::floor(sy));
            int x1 = std::min(x0 + 1, sw - 1);
            int y1 = std::min(y0 + 1, sh - 1);
            float fx = sx - x0, fy = sy - y0;
            for (int c = 0; c < 3; ++c) {
                float p00 = src[((size_t)y0 * sw + x0) * 3 + c];
                float p01 = src[((size_t)y0 * sw + x1) * 3 + c];
                float p10 = src[((size_t)y1 * sw + x0) * 3 + c];
                float p11 = src[((size_t)y1 * sw + x1) * 3 + c];
                float v = p00 * (1 - fx) * (1 - fy) + p01 * fx * (1 - fy)
                        + p10 * (1 - fx) * fy + p11 * fx * fy;
                dst[((size_t)dy * dw + dx) * 3 + c] =
                    static_cast<uint8_t>(std::max(0.0f, std::min(255.0f, std::round(v))));
            }
        }
    }
}

// ─────────────────── YuNet ───────────────────

bool YuNet::load(const std::wstring& path, std::string& err) {
    if (!sess_.load(path, 2, err)) return false;
    size_ = sess_.input_size() > 0 ? sess_.input_size() : 320;
    return true;
}

static float iou(const Face& a, const Face& b) {
    float ix1 = std::max(a.x, b.x), iy1 = std::max(a.y, b.y);
    float ix2 = std::min(a.x + a.w, b.x + b.w), iy2 = std::min(a.y + a.h, b.y + b.h);
    float inter = std::max(0.0f, ix2 - ix1) * std::max(0.0f, iy2 - iy1);
    float uni = a.w * a.h + b.w * b.h - inter;
    return uni > 0 ? inter / uni : 0.0f;
}

std::vector<Face> YuNet::detect(const uint8_t* rgb, int w, int h, float conf_threshold) {
    std::vector<Face> faces;
    if (!sess_.ok() || w <= 0 || h <= 0) return faces;
    const int s = size_;

    // 1. 缩放到模型输入（不保持宽高比，与 OpenCV 示例一致）
    std::vector<uint8_t> resized((size_t)s * s * 3);
    bilinear_resize_rgb(rgb, w, h, resized.data(), s, s);

    // 2. NCHW BGR 0..255（blobFromImage 默认参数）
    std::vector<float> input((size_t)3 * s * s);
    for (int c = 0; c < 3; ++c) {
        int src_c = 2 - c;
        for (int y = 0; y < s; ++y)
            for (int x = 0; x < s; ++x)
                input[((size_t)c * s + y) * s + x] = resized[((size_t)y * s + x) * 3 + src_c];
    }

    // 3. 推理
    std::vector<float*> outs;
    std::string err;
    if (!sess_.run(input.data(), input.size(), outs, err)) {
        fprintf(stderr, "[yunet] 推理错误: %s\n", err.c_str());
        return faces;
    }
    std::map<std::string, int> by_name;
    for (size_t i = 0; i < sess_.output_names().size(); ++i)
        by_name[sess_.output_names()[i]] = (int)i;
    auto tensor = [&](const std::string& name) -> float* {
        auto it = by_name.find(name);
        return it == by_name.end() ? nullptr : outs[it->second];
    };

    // 4. 逐 stride 解码（对齐 OpenCV face_detect.cpp）
    const float scale_x = (float)w / s, scale_y = (float)h / s;
    const int strides[3] = {8, 16, 32};
    for (int stride : strides) {
        int cols = s / stride, rows = s / stride;
        float* cls = tensor("cls_" + std::to_string(stride));
        float* obj = tensor("obj_" + std::to_string(stride));
        float* bbox = tensor("bbox_" + std::to_string(stride));
        float* kps = tensor("kps_" + std::to_string(stride));
        if (!cls || !obj || !bbox) continue;
        float sf = (float)stride;
        for (int r = 0; r < rows; ++r) {
            for (int c = 0; c < cols; ++c) {
                int idx = r * cols + c;
                float cls_score = std::max(0.0f, std::min(1.0f, cls[idx]));
                float obj_score = std::max(0.0f, std::min(1.0f, obj[idx]));
                float score = std::sqrt(cls_score * obj_score);
                if (score < conf_threshold) continue;

                int base = idx * 4;
                float cx = (c + bbox[base]) * sf;
                float cy = (r + bbox[base + 1]) * sf;
                float bw = std::exp(bbox[base + 2]) * sf;
                float bh = std::exp(bbox[base + 3]) * sf;
                Face f;
                f.x = (cx - bw / 2) * scale_x;
                f.y = (cy - bh / 2) * scale_y;
                f.w = bw * scale_x;
                f.h = bh * scale_y;
                f.confidence = score;
                if (f.w < 20.0f || f.h < 20.0f) continue;

                if (kps) {
                    int kb = idx * 10;
                    for (int n = 0; n < 5; ++n) {
                        f.kps[n][0] = (kps[kb + 2 * n] + c) * sf * scale_x;
                        f.kps[n][1] = (kps[kb + 2 * n + 1] + r) * sf * scale_y;
                    }
                    f.has_kps = true;
                }
                faces.push_back(f);
            }
        }
    }

    // 5. NMS（阈值 0.3，与 Rust 版一致）
    std::sort(faces.begin(), faces.end(),
              [](const Face& a, const Face& b) { return a.confidence > b.confidence; });
    std::vector<Face> keep;
    for (const auto& f : faces) {
        bool suppressed = false;
        for (const auto& k : keep)
            if (iou(f, k) >= 0.3f) { suppressed = true; break; }
        if (!suppressed) keep.push_back(f);
    }
    return keep;
}

// ─────────────────── FaceMesh（478/468 点） ───────────────────

bool FaceMesh::load(const std::wstring& path, std::string& err) {
    if (!sess_.load(path, 2, err)) return false;
    size_ = sess_.input_size();
    if (size_ <= 0) { err = "网格模型输入尺寸未知"; return false; }
    // 按形状识别输出：3 维 = landmarks，其余 = score
    for (size_t i = 0; i < sess_.output_shapes().size(); ++i) {
        const auto& sh = sess_.output_shapes()[i];
        if (sh.size() == 3) {
            lm_out_ = (int)i;
            num_landmarks_ = sh[1] > 0 ? (int)sh[1] : 478;
        } else {
            score_out_ = (int)i;
        }
    }
    if (lm_out_ < 0 || score_out_ < 0) { err = "网格模型输出结构不符"; return false; }
    return true;
}

bool FaceMesh::infer(const uint8_t* rgb, int w, int h, const Face& det,
                     std::vector<Point3>& points, float& score, std::string& err) {
    points.clear();
    score = 0;
    if (!sess_.ok()) { err = "网格模型未加载"; return false; }

    // ── ROI：detection_to_roi 规则 ──
    // 正方形（长边 ×1.5），绕眼线旋转至水平。
    const float side = 1.5f * std::max(det.w, det.h);
    const float cx = det.x + det.w / 2, cy = det.y + det.h / 2;
    float angle = 0;
    if (det.has_kps) {
        // kps[0]=右眼(画面左), kps[1]=左眼(画面右) → 眼线角度
        angle = std::atan2(det.kps[1][1] - det.kps[0][1],
                           det.kps[1][0] - det.kps[0][0]);
    }
    const float out = (float)size_;
    const float scale = out / side;
    const float alpha = scale * std::cos(angle), beta = scale * std::sin(angle);
    // 等价 cv2.getRotationMatrix2D((cx,cy), angle, scale) + 平移到输出中心
    const float m00 = alpha, m01 = beta;
    const float m02 = (1 - alpha) * cx - beta * cy + out / 2 - cx;
    const float m10 = -beta, m11 = alpha;
    const float m12 = beta * cx + (1 - alpha) * cy + out / 2 - cy;
    // 逆仿射（crop 像素 → 原图像素）
    const float detm = m00 * m11 - m01 * m10;  // = scale²
    const float i00 = m11 / detm, i01 = -m01 / detm;
    const float i02 = (m01 * m12 - m02 * m11) / detm;
    const float i10 = -m10 / detm, i11 = m00 / detm;
    const float i12 = (m02 * m10 - m00 * m12) / detm;

    // ── 单次双线性 warp 直接到模型分辨率，越界补 0；同时 /255 → CHW RGB ──
    const int s = size_;
    std::vector<float> input((size_t)3 * s * s, 0.0f);
    for (int dy = 0; dy < s; ++dy) {
        for (int dx = 0; dx < s; ++dx) {
            float sx = i00 * dx + i01 * dy + i02;
            float sy = i10 * dx + i11 * dy + i12;
            if (sx < 0 || sy < 0 || sx > w - 1 || sy > h - 1) continue;
            int x0 = (int)std::floor(sx), y0 = (int)std::floor(sy);
            int x1 = std::min(x0 + 1, w - 1), y1 = std::min(y0 + 1, h - 1);
            float fx = sx - x0, fy = sy - y0;
            for (int c = 0; c < 3; ++c) {
                float p00 = rgb[((size_t)y0 * w + x0) * 3 + c];
                float p01 = rgb[((size_t)y0 * w + x1) * 3 + c];
                float p10 = rgb[((size_t)y1 * w + x0) * 3 + c];
                float p11 = rgb[((size_t)y1 * w + x1) * 3 + c];
                float v = p00 * (1 - fx) * (1 - fy) + p01 * fx * (1 - fy)
                        + p10 * (1 - fx) * fy + p11 * fx * fy;
                input[((size_t)c * s + dy) * s + dx] = v / 255.0f;
            }
        }
    }

    // ── 推理 ──
    std::vector<float*> outs;
    if (!sess_.run(input.data(), input.size(), outs, err)) return false;
    const float* lm = outs[lm_out_];
    float logit = outs[score_out_][0];
    score = 1.0f / (1.0f + std::exp(-logit));  // 原始输出是 logit
    if (score < 0.5f) return false;

    // ── 逆映射回原图 + 归一化（x/w, y/h, z 与 x 同尺度 → z/w） ──
    points.resize(num_landmarks_);
    const float z_scale = side / out;
    for (int i = 0; i < num_landmarks_; ++i) {
        float lx = lm[i * 3], ly = lm[i * 3 + 1], lz = lm[i * 3 + 2];
        points[i].x = (i00 * lx + i01 * ly + i02) / w;
        points[i].y = (i10 * lx + i11 * ly + i12) / h;
        points[i].z = lz * z_scale / w;
    }
    return true;
}