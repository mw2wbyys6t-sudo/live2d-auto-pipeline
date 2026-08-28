#!/usr/bin/env python3
"""
Live2D PSD 质量检查工具 - 增强版
版本: 2.0
功能: 全面的 PSD 质量检查，包括遮挡关系、透明度、混合模式、分辨率等
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class QAIssue:
    """质量问题"""
    severity: str  # error, warning, info
    category: str  # naming, structure, transparency, blend, resolution, occlusion
    message: str
    layer: Optional[str] = None
    suggestion: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QAReport:
    """质量检查报告"""
    score: int
    issues: List[QAIssue]
    passed: bool
    statistics: Dict[str, Any]
    recommendations: List[str]


class EnhancedQAEngine:
    """增强版质量检查引擎"""
    
    def __init__(self):
        self.issues: List[QAIssue] = []
        self.statistics: Dict[str, Any] = {}
        
        # 标准图层命名规范
        self.standard_layers = {
            'head': ['hair_front', 'hair_back', 'hair_side', 'face_base', 'face_shadow'],
            'eyes': ['eye_l_white', 'eye_r_white', 'eye_l_iris', 'eye_r_iris', 
                    'eye_l_pupil', 'eye_r_pupil', 'eye_l_highlight', 'eye_r_highlight'],
            'mouth': ['mouth_base', 'mouth_a', 'mouth_i', 'mouth_u', 'mouth_e', 'mouth_o'],
            'body': ['body_front', 'body_back', 'neck', 'arm_front_l', 'arm_front_r', 
                    'arm_back_l', 'arm_back_r'],
            'clothes': ['clothes_top', 'clothes_bottom', 'skirt', 'accessory'],
            'eyebrows': ['eyebrow_l', 'eyebrow_r'],
            'nose': ['nose']
        }
        
        # 标准 Draw Order
        self.standard_draw_order = {
            'hair_back': 10,
            'body_back': 20,
            'arm_back_l': 25,
            'arm_back_r': 26,
            'skirt': 30,
            'body_front': 40,
            'arm_front_l': 45,
            'arm_front_r': 46,
            'neck': 50,
            'face_base': 60,
            'face_shadow': 61,
            'eye_l_white': 70,
            'eye_r_white': 71,
            'eye_l_iris': 72,
            'eye_r_iris': 73,
            'eye_l_pupil': 74,
            'eye_r_pupil': 75,
            'eye_l_highlight': 76,
            'eye_r_highlight': 77,
            'eyebrow_l': 80,
            'eyebrow_r': 81,
            'nose': 85,
            'mouth_base': 90,
            'mouth_a': 91,
            'mouth_i': 92,
            'mouth_u': 93,
            'mouth_e': 94,
            'mouth_o': 95,
            'hair_front': 100,
            'accessory': 110
        }
    
    def check_all(self, psd_data: Dict[str, Any]) -> QAReport:
        """执行所有检查"""
        self.issues = []
        
        # 1. 图层命名检查
        self._check_layer_naming(psd_data)
        
        # 2. 图层结构检查
        self._check_layer_structure(psd_data)
        
        # 3. 遮挡关系分析
        self._check_occlusion(psd_data)
        
        # 4. 透明度检查
        self._check_transparency(psd_data)
        
        # 5. 混合模式检查
        self._check_blend_modes(psd_data)
        
        # 6. 分辨率检查
        self._check_resolution(psd_data)
        
        # 7. Draw Order 检查
        self._check_draw_order(psd_data)
        
        # 计算分数
        score = self._calculate_score()
        
        # 生成建议
        recommendations = self._generate_recommendations()
        
        return QAReport(
            score=score,
            issues=self.issues,
            passed=score >= 70,
            statistics=self.statistics,
            recommendations=recommendations
        )
    
    def _check_layer_naming(self, psd_data: Dict[str, Any]):
        """检查图层命名规范"""
        layers = psd_data.get('layers', [])
        
        for layer in layers:
            name = layer.get('name', '')
            
            # 检查是否使用中文
            if any('\u4e00' <= char <= '\u9fff' for char in name):
                self.issues.append(QAIssue(
                    severity='error',
                    category='naming',
                    message=f'图层名包含中文: {name}',
                    layer=name,
                    suggestion='请使用英文命名，如: hair_front_01'
                ))
            
            # 检查是否包含空格
            if ' ' in name:
                self.issues.append(QAIssue(
                    severity='warning',
                    category='naming',
                    message=f'图层名包含空格: {name}',
                    layer=name,
                    suggestion='请使用下划线代替空格，如: hair_front'
                ))
            
            # 检查是否以数字开头
            if name and name[0].isdigit():
                self.issues.append(QAIssue(
                    severity='warning',
                    category='naming',
                    message=f'图层名以数字开头: {name}',
                    layer=name,
                    suggestion='图层名应以字母开头'
                ))
    
    def _check_layer_structure(self, psd_data: Dict[str, Any]):
        """检查图层结构完整性"""
        layers = psd_data.get('layers', [])
        layer_names = [l.get('name', '').lower() for l in layers]
        
        # 检查必需的图层
        required_categories = ['head', 'eyes', 'mouth', 'body']
        
        for category in required_categories:
            required_layers = self.standard_layers.get(category, [])
            found = False
            
            for required in required_layers:
                if any(required in name for name in layer_names):
                    found = True
                    break
            
            if not found:
                self.issues.append(QAIssue(
                    severity='warning',
                    category='structure',
                    message=f'缺少 {category} 相关图层',
                    suggestion=f'建议添加: {", ".join(required_layers[:3])}'
                ))
        
        self.statistics['total_layers'] = len(layers)
        self.statistics['categories_found'] = sum(
            1 for cat in required_categories 
            if any(any(req in name for name in layer_names) for req in self.standard_layers.get(cat, []))
        )
    
    def _check_occlusion(self, psd_data: Dict[str, Any]):
        """分析遮挡关系"""
        layers = psd_data.get('layers', [])
        
        # 按 Draw Order 排序
        sorted_layers = sorted(
            layers,
            key=lambda l: l.get('drawOrder', 0)
        )
        
        # 检测潜在的遮挡问题
        for i, layer1 in enumerate(sorted_layers):
            for layer2 in sorted_layers[i+1:]:
                # 检查是否有重叠区域
                if self._layers_overlap(layer1, layer2):
                    name1 = layer1.get('name', '')
                    name2 = layer2.get('name', '')
                    order1 = layer1.get('drawOrder', 0)
                    order2 = layer2.get('drawOrder', 0)
                    
                    # 检查是否符合预期的遮挡关系
                    expected_order1 = self.standard_draw_order.get(name1.lower(), 0)
                    expected_order2 = self.standard_draw_order.get(name2.lower(), 0)
                    
                    if expected_order1 > expected_order2 and order1 < order2:
                        self.issues.append(QAIssue(
                            severity='warning',
                            category='occlusion',
                            message=f'遮挡关系可能有问题: {name1} 应在 {name2} 上方',
                            suggestion=f'建议调整 Draw Order: {name1} > {name2}'
                        ))
        
        self.statistics['occlusion_checks'] = len(layers) * (len(layers) - 1) // 2
    
    def _layers_overlap(self, layer1: Dict, layer2: Dict) -> bool:
        """检查两个图层是否重叠"""
        # 简化版：假设所有图层都有重叠
        # 实际实现需要检查边界框
        bounds1 = layer1.get('bounds', {})
        bounds2 = layer2.get('bounds', {})
        
        if not bounds1 or not bounds2:
            return True  # 无法确定，假设重叠
        
        # 检查边界框是否相交
        x1_max = bounds1.get('right', 0)
        x1_min = bounds1.get('left', 0)
        y1_max = bounds1.get('bottom', 0)
        y1_min = bounds1.get('top', 0)
        
        x2_max = bounds2.get('right', 0)
        x2_min = bounds2.get('left', 0)
        y2_max = bounds2.get('bottom', 0)
        y2_min = bounds2.get('top', 0)
        
        return not (x1_max < x2_min or x2_max < x1_min or 
                   y1_max < y2_min or y2_max < y1_min)
    
    def _check_transparency(self, psd_data: Dict[str, Any]):
        """检查透明度设置"""
        layers = psd_data.get('layers', [])
        
        for layer in layers:
            name = layer.get('name', '')
            opacity = layer.get('opacity', 255)
            
            # 检查半透明图层
            if 0 < opacity < 255:
                opacity_percent = (opacity / 255) * 100
                self.issues.append(QAIssue(
                    severity='info',
                    category='transparency',
                    message=f'图层 {name} 透明度为 {opacity_percent:.1f}%',
                    layer=name,
                    suggestion='Live2D 通常建议使用完全不透明图层',
                    details={'opacity': opacity_percent}
                ))
            
            # 检查完全透明图层
            if opacity == 0:
                self.issues.append(QAIssue(
                    severity='warning',
                    category='transparency',
                    message=f'图层 {name} 完全透明',
                    layer=name,
                    suggestion='完全透明的图层可能不需要，建议删除'
                ))
        
        self.statistics['transparent_layers'] = sum(
            1 for l in layers if 0 < l.get('opacity', 255) < 255
        )
    
    def _check_blend_modes(self, psd_data: Dict[str, Any]):
        """检查混合模式"""
        layers = psd_data.get('layers', [])
        
        supported_modes = ['normal', 'pass through']
        problematic_modes = ['multiply', 'screen', 'overlay', 'darken', 'lighten']
        
        for layer in layers:
            name = layer.get('name', '')
            blend_mode = layer.get('blendMode', 'normal').lower()
            
            if blend_mode not in supported_modes:
                severity = 'warning' if blend_mode in problematic_modes else 'error'
                
                self.issues.append(QAIssue(
                    severity=severity,
                    category='blend',
                    message=f'图层 {name} 使用 {blend_mode} 混合模式',
                    layer=name,
                    suggestion='Live2D Cubism 仅支持 Normal 混合模式，建议将效果烘焙到图层中',
                    details={'blendMode': blend_mode}
                ))
        
        self.statistics['non_normal_blend_layers'] = sum(
            1 for l in layers 
            if l.get('blendMode', 'normal').lower() not in supported_modes
        )
    
    def _check_resolution(self, psd_data: Dict[str, Any]):
        """检查分辨率"""
        width = psd_data.get('width', 0)
        height = psd_data.get('height', 0)
        
        # 推荐的分辨率
        recommended_sizes = [
            (1024, 1024),
            (2048, 2048),
            (4096, 4096)
        ]
        
        # 检查是否为推荐尺寸
        is_recommended = any(
            (width, height) == size or (height, width) == size 
            for size in recommended_sizes
        )
        
        if not is_recommended:
            self.issues.append(QAIssue(
                severity='info',
                category='resolution',
                message=f'当前分辨率: {width}x{height}',
                suggestion='推荐使用 1024x1024、2048x2048 或 4096x4096',
                details={'width': width, 'height': height}
            ))
        
        # 检查是否为正方形
        if width != height:
            self.issues.append(QAIssue(
                severity='warning',
                category='resolution',
                message=f'画布不是正方形: {width}x{height}',
                suggestion='Live2D 通常使用正方形画布'
            ))
        
        # 检查是否过大
        if width > 4096 or height > 4096:
            self.issues.append(QAIssue(
                severity='warning',
                category='resolution',
                message=f'分辨率过大: {width}x{height}',
                suggestion='过大的分辨率可能影响性能，建议不超过 4096x4096'
            ))
        
        self.statistics['resolution'] = f'{width}x{height}'
        self.statistics['is_square'] = width == height
    
    def _check_draw_order(self, psd_data: Dict[str, Any]):
        """检查 Draw Order 设置"""
        layers = psd_data.get('layers', [])
        
        # 检查是否有重复的 Draw Order
        draw_orders = {}
        for layer in layers:
            order = layer.get('drawOrder', 0)
            name = layer.get('name', '')
            
            if order in draw_orders:
                self.issues.append(QAIssue(
                    severity='warning',
                    category='structure',
                    message=f'Draw Order 冲突: {name} 和 {draw_orders[order]} 都是 {order}',
                    suggestion='每个图层应有唯一的 Draw Order'
                ))
            else:
                draw_orders[order] = name
    
    def _calculate_score(self) -> int:
        """计算质量分数"""
        score = 100
        
        for issue in self.issues:
            if issue.severity == 'error':
                score -= 15
            elif issue.severity == 'warning':
                score -= 8
            elif issue.severity == 'info':
                score -= 3
        
        return max(0, min(100, score))
    
    def _generate_recommendations(self) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 根据问题类型生成建议
        categories = set(issue.category for issue in self.issues)
        
        if 'naming' in categories:
            recommendations.append('建议统一使用英文命名，遵循 Live2D 命名规范')
        
        if 'structure' in categories:
            recommendations.append('建议补充缺失的图层，确保结构完整')
        
        if 'occlusion' in categories:
            recommendations.append('建议检查并调整图层遮挡关系')
        
        if 'transparency' in categories:
            recommendations.append('建议将半透明效果烘焙到图层中')
        
        if 'blend' in categories:
            recommendations.append('建议将混合模式效果烘焙到图层中，仅使用 Normal 模式')
        
        if 'resolution' in categories:
            recommendations.append('建议使用推荐的分辨率: 1024x1024、2048x2048 或 4096x4096')
        
        return recommendations
    
    def generate_report_markdown(self, report: QAReport) -> str:
        """生成 Markdown 格式的报告"""
        md = []
        
        md.append('# PSD 质量检查报告\n')
        md.append(f'**综合评分**: {report.score}/100\n')
        md.append(f'**状态**: {"✅ 通过" if report.passed else "❌ 未通过"}\n\n')
        
        # 统计信息
        md.append('## 📊 统计信息\n\n')
        for key, value in report.statistics.items():
            md.append(f'- **{key}**: {value}\n')
        md.append('\n')
        
        # 问题列表
        if report.issues:
            md.append('## 📋 问题列表\n\n')
            
            # 按严重程度分组
            errors = [i for i in report.issues if i.severity == 'error']
            warnings = [i for i in report.issues if i.severity == 'warning']
            infos = [i for i in report.issues if i.severity == 'info']
            
            if errors:
                md.append('### ❌ 严重问题\n\n')
                for issue in errors:
                    md.append(f'- **{issue.message}**\n')
                    if issue.suggestion:
                        md.append(f'  - 💡 {issue.suggestion}\n')
                md.append('\n')
            
            if warnings:
                md.append('### ⚠️ 警告\n\n')
                for issue in warnings:
                    md.append(f'- {issue.message}\n')
                    if issue.suggestion:
                        md.append(f'  - 💡 {issue.suggestion}\n')
                md.append('\n')
            
            if infos:
                md.append('### ℹ️ 提示\n\n')
                for issue in infos:
                    md.append(f'- {issue.message}\n')
                md.append('\n')
        
        # 建议
        if report.recommendations:
            md.append('## 💡 改进建议\n\n')
            for rec in report.recommendations:
                md.append(f'- {rec}\n')
            md.append('\n')
        
        return ''.join(md)


# 使用示例
if __name__ == "__main__":
    # 测试数据
    test_psd = {
        'width': 2048,
        'height': 2048,
        'layers': [
            {'name': 'hair_back', 'drawOrder': 10, 'opacity': 255, 'blendMode': 'normal'},
            {'name': 'face_base', 'drawOrder': 60, 'opacity': 255, 'blendMode': 'normal'},
            {'name': 'eye_l_white', 'drawOrder': 70, 'opacity': 255, 'blendMode': 'normal'},
            {'name': 'eye_r_white', 'drawOrder': 71, 'opacity': 200, 'blendMode': 'multiply'},
            {'name': 'mouth_base', 'drawOrder': 90, 'opacity': 255, 'blendMode': 'normal'},
            {'name': 'hair_front', 'drawOrder': 100, 'opacity': 255, 'blendMode': 'normal'},
        ]
    }
    
    engine = EnhancedQAEngine()
    report = engine.check_all(test_psd)
    
    print(engine.generate_report_markdown(report))
