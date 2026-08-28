#!/usr/bin/env python3
"""
Live2D Cubism 参数设计工具 - 增强版
版本: 2.0
功能: 预设参数模板、参数组合建议、表情参数设计
"""

import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Parameter:
    """Cubism 参数"""
    name: str
    min_value: float
    max_value: float
    default_value: float
    description: str
    category: str  # angle, eye, mouth, body, expression
    is_standard: bool = True


@dataclass
class ParameterTemplate:
    """参数模板"""
    name: str
    description: str
    parameters: List[Parameter]
    suitable_for: List[str]  # 适用角色类型


@dataclass
class ExpressionConfig:
    """表情配置"""
    name: str
    parameters: Dict[str, float]
    intensity: float = 1.0


class EnhancedParameterDesigner:
    """增强版参数设计器"""
    
    def __init__(self):
        self.templates: List[ParameterTemplate] = []
        self._load_standard_parameters()
        self._load_templates()
    
    def _load_standard_parameters(self):
        """加载标准 Cubism 参数"""
        self.standard_params = {
            # 角度参数
            'angle': [
                Parameter('ParamAngleX', -30, 30, 0, '头部左右转动', 'angle'),
                Parameter('ParamAngleY', -30, 30, 0, '头部上下转动', 'angle'),
                Parameter('ParamAngleZ', -30, 30, 0, '头部倾斜', 'angle'),
            ],
            # 眼睛参数
            'eye': [
                Parameter('ParamEyeLOpen', 0, 1, 1, '左眼睁开程度', 'eye'),
                Parameter('ParamEyeROpen', 0, 1, 1, '右眼睁开程度', 'eye'),
                Parameter('ParamEyeBallX', -1, 1, 0, '眼球左右移动', 'eye'),
                Parameter('ParamEyeBallY', -1, 1, 0, '眼球上下移动', 'eye'),
                Parameter('ParamEyeBallForm', 0, 1, 0, '眼球形状变化', 'eye'),
            ],
            # 眉毛参数
            'eyebrow': [
                Parameter('ParamBrowLY', -1, 1, 0, '左眉上下移动', 'expression'),
                Parameter('ParamBrowRY', -1, 1, 0, '右眉上下移动', 'expression'),
                Parameter('ParamBrowLX', -1, 1, 0, '左眉左右移动', 'expression'),
                Parameter('ParamBrowRX', -1, 1, 0, '右眉左右移动', 'expression'),
                Parameter('ParamBrowLAngle', -1, 1, 0, '左眉角度', 'expression'),
                Parameter('ParamBrowRAngle', -1, 1, 0, '右眉角度', 'expression'),
            ],
            # 嘴巴参数
            'mouth': [
                Parameter('ParamMouthOpenY', 0, 1, 0, '嘴巴张开程度', 'mouth'),
                Parameter('ParamMouthForm', -1, 1, 0, '嘴巴形状（-1撇嘴，1微笑）', 'mouth'),
                Parameter('ParamMouthForm2', 0, 1, 0, '嘴巴形状变化2', 'mouth'),
            ],
            # 身体参数
            'body': [
                Parameter('ParamBodyAngleX', -10, 10, 0, '身体左右转动', 'body'),
                Parameter('ParamBodyAngleY', -10, 10, 0, '身体前后倾斜', 'body'),
                Parameter('ParamBodyAngleZ', -10, 10, 0, '身体侧倾', 'body'),
                Parameter('ParamBreath', 0, 1, 0, '呼吸动画', 'body'),
            ],
            # 特殊参数
            'special': [
                Parameter('ParamHairFront', -1, 1, 0, '前发飘动', 'body'),
                Parameter('ParamHairBack', -1, 1, 0, '后发飘动', 'body'),
                Parameter('ParamHairSide', -1, 1, 0, '侧发飘动', 'body'),
                Parameter('ParamArmLA', 0, 1, 0, '左臂动作', 'body'),
                Parameter('ParamArmRA', 0, 1, 0, '右臂动作', 'body'),
            ]
        }
    
    def _load_templates(self):
        """加载预设模板"""
        self.templates = [
            # 基础模板
            ParameterTemplate(
                name='基础模板',
                description='适合所有角色的基础参数配置',
                parameters=(
                    self.standard_params['angle'] +
                    self.standard_params['eye'] +
                    self.standard_params['mouth'] +
                    self.standard_params['body']
                ),
                suitable_for=['所有角色']
            ),
            # 标准模板
            ParameterTemplate(
                name='标准模板',
                description='包含眉毛和基础物理的标准配置',
                parameters=(
                    self.standard_params['angle'] +
                    self.standard_params['eye'] +
                    self.standard_params['eyebrow'] +
                    self.standard_params['mouth'] +
                    self.standard_params['body']
                ),
                suitable_for=['标准角色', 'VTuber']
            ),
            # 完整模板
            ParameterTemplate(
                name='完整模板',
                description='包含所有参数的完整配置',
                parameters=(
                    self.standard_params['angle'] +
                    self.standard_params['eye'] +
                    self.standard_params['eyebrow'] +
                    self.standard_params['mouth'] +
                    self.standard_params['body'] +
                    self.standard_params['special']
                ),
                suitable_for=['专业角色', '高质量VTuber']
            ),
            # 简单模板
            ParameterTemplate(
                name='简单模板',
                description='适合简单角色的最小参数集',
                parameters=(
                    self.standard_params['angle'][:1] +
                    self.standard_params['eye'][:2] +
                    self.standard_params['mouth'][:1]
                ),
                suitable_for=['简单角色', 'PNG VTuber']
            ),
            # 表情丰富模板
            ParameterTemplate(
                name='表情丰富模板',
                description='强调表情变化的参数配置',
                parameters=(
                    self.standard_params['angle'] +
                    self.standard_params['eye'] +
                    self.standard_params['eyebrow'] +
                    self.standard_params['mouth'] +
                    [Parameter('ParamEyeHappy', 0, 1, 0, '开心眼睛', 'expression'),
                     Parameter('ParamEyeSad', 0, 1, 0, '悲伤眼睛', 'expression'),
                     Parameter('ParamEyeAngry', 0, 1, 0, '生气眼睛', 'expression'),
                     Parameter('ParamEyeSurprised', 0, 1, 0, '惊讶眼睛', 'expression')]
                ),
                suitable_for=['表情丰富的角色', '互动VTuber']
            ),
            # 物理丰富模板
            ParameterTemplate(
                name='物理丰富模板',
                description='强调物理效果的参数配置',
                parameters=(
                    self.standard_params['angle'] +
                    self.standard_params['eye'] +
                    self.standard_params['mouth'] +
                    self.standard_params['body'] +
                    [Parameter('ParamHairFront', -1, 1, 0, '前发物理', 'body'),
                     Parameter('ParamHairBack', -1, 1, 0, '后发物理', 'body'),
                     Parameter('ParamHairSide', -1, 1, 0, '侧发物理', 'body'),
                     Parameter('ParamSkirt', -1, 1, 0, '裙子物理', 'body'),
                     Parameter('ParamAccessory1', -1, 1, 0, '配饰1物理', 'body'),
                     Parameter('ParamAccessory2', -1, 1, 0, '配饰2物理', 'body')]
                ),
                suitable_for=['长发角色', '有裙子的角色', '有配饰的角色']
            ),
        ]
    
    def get_template(self, template_name: str) -> Optional[ParameterTemplate]:
        """获取指定模板"""
        for template in self.templates:
            if template.name == template_name:
                return template
        return None
    
    def get_recommended_template(self, character_features: List[str]) -> ParameterTemplate:
        """根据角色特征推荐模板"""
        # 按匹配度排序
        scored_templates = []
        
        for template in self.templates:
            score = sum(
                1 for feature in character_features 
                if feature in template.suitable_for
            )
            scored_templates.append((score, template))
        
        # 返回匹配度最高的
        scored_templates.sort(key=lambda x: x[0], reverse=True)
        return scored_templates[0][1]
    
    def generate_parameter_combinations(self) -> List[Dict[str, Any]]:
        """生成参数组合建议"""
        combinations = [
            {
                'name': '头部动作组合',
                'description': '头部自然转动的参数组合',
                'parameters': ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ'],
                'usage': '用于实现自然的头部转动动画'
            },
            {
                'name': '眼睛动作组合',
                'description': '眼睛相关参数的组合',
                'parameters': ['ParamEyeLOpen', 'ParamEyeROpen', 'ParamEyeBallX', 'ParamEyeBallY'],
                'usage': '用于实现眨眼、视线移动等效果'
            },
            {
                'name': '表情变化组合',
                'description': '表情相关参数的组合',
                'parameters': ['ParamBrowLY', 'ParamBrowRY', 'ParamMouthForm', 'ParamMouthOpenY'],
                'usage': '用于实现丰富的表情变化'
            },
            {
                'name': '身体动作组合',
                'description': '身体相关参数的组合',
                'parameters': ['ParamBodyAngleX', 'ParamBodyAngleY', 'ParamBreath'],
                'usage': '用于实现身体转动和呼吸动画'
            },
            {
                'name': '头发物理组合',
                'description': '头发物理参数的组合',
                'parameters': ['ParamHairFront', 'ParamHairBack', 'ParamHairSide'],
                'usage': '用于实现头发飘动效果'
            },
        ]
        return combinations
    
    def generate_expression_configs(self) -> List[ExpressionConfig]:
        """生成表情配置"""
        expressions = [
            ExpressionConfig(
                name='默认',
                parameters={
                    'ParamEyeLOpen': 1.0,
                    'ParamEyeROpen': 1.0,
                    'ParamMouthOpenY': 0.0,
                    'ParamMouthForm': 0.0,
                    'ParamBrowLY': 0.0,
                    'ParamBrowRY': 0.0,
                }
            ),
            ExpressionConfig(
                name='开心',
                parameters={
                    'ParamEyeLOpen': 0.8,
                    'ParamEyeROpen': 0.8,
                    'ParamMouthOpenY': 0.3,
                    'ParamMouthForm': 1.0,
                    'ParamBrowLY': 0.3,
                    'ParamBrowRY': 0.3,
                }
            ),
            ExpressionConfig(
                name='悲伤',
                parameters={
                    'ParamEyeLOpen': 0.6,
                    'ParamEyeROpen': 0.6,
                    'ParamMouthOpenY': 0.0,
                    'ParamMouthForm': -0.5,
                    'ParamBrowLY': -0.5,
                    'ParamBrowRY': -0.5,
                    'ParamBrowLAngle': -0.3,
                    'ParamBrowRAngle': -0.3,
                }
            ),
            ExpressionConfig(
                name='生气',
                parameters={
                    'ParamEyeLOpen': 0.9,
                    'ParamEyeROpen': 0.9,
                    'ParamMouthOpenY': 0.0,
                    'ParamMouthForm': -0.8,
                    'ParamBrowLY': -0.7,
                    'ParamBrowRY': -0.7,
                    'ParamBrowLAngle': 0.5,
                    'ParamBrowRAngle': 0.5,
                }
            ),
            ExpressionConfig(
                name='惊讶',
                parameters={
                    'ParamEyeLOpen': 1.0,
                    'ParamEyeROpen': 1.0,
                    'ParamMouthOpenY': 0.8,
                    'ParamMouthForm': 0.0,
                    'ParamBrowLY': 0.8,
                    'ParamBrowRY': 0.8,
                }
            ),
            ExpressionConfig(
                name='眨眼',
                parameters={
                    'ParamEyeLOpen': 0.0,
                    'ParamEyeROpen': 0.0,
                }
            ),
            ExpressionConfig(
                name='眨左眼',
                parameters={
                    'ParamEyeLOpen': 0.0,
                    'ParamEyeROpen': 1.0,
                }
            ),
            ExpressionConfig(
                name='眨右眼',
                parameters={
                    'ParamEyeLOpen': 1.0,
                    'ParamEyeROpen': 0.0,
                }
            ),
        ]
        return expressions
    
    def generate_config_json(self, template_name: str = '标准模板') -> Dict[str, Any]:
        """生成 Cubism 配置 JSON"""
        template = self.get_template(template_name)
        
        if not template:
            template = self.templates[1]  # 默认使用标准模板
        
        config = {
            'version': '1.0',
            'template': template.name,
            'description': template.description,
            'parameters': [
                {
                    'name': p.name,
                    'min': p.min_value,
                    'max': p.max_value,
                    'default': p.default_value,
                    'description': p.description,
                    'category': p.category
                }
                for p in template.parameters
            ],
            'combinations': self.generate_parameter_combinations(),
            'expressions': [
                {
                    'name': e.name,
                    'parameters': e.parameters,
                    'intensity': e.intensity
                }
                for e in self.generate_expression_configs()
            ]
        }
        
        return config
    
    def generate_report_markdown(self, template_name: str = '标准模板') -> str:
        """生成 Markdown 格式的报告"""
        template = self.get_template(template_name)
        
        if not template:
            template = self.templates[1]
        
        md = []
        
        md.append(f'# Cubism 参数配置 - {template.name}\n\n')
        md.append(f'**描述**: {template.description}\n\n')
        md.append(f'**适用**: {", ".join(template.suitable_for)}\n\n')
        
        # 参数列表
        md.append('## 📋 参数列表\n\n')
        
        categories = {}
        for param in template.parameters:
            if param.category not in categories:
                categories[param.category] = []
            categories[param.category].append(param)
        
        for category, params in categories.items():
            md.append(f'### {category.upper()}\n\n')
            md.append('| 参数名 | 范围 | 默认值 | 说明 |\n')
            md.append('|--------|------|--------|------|\n')
            
            for p in params:
                md.append(f'| `{p.name}` | [{p.min_value}, {p.max_value}] | {p.default_value} | {p.description} |\n')
            md.append('\n')
        
        # 参数组合
        md.append('## 🔗 参数组合建议\n\n')
        for combo in self.generate_parameter_combinations():
            md.append(f'### {combo["name"]}\n\n')
            md.append(f'**说明**: {combo["description"]}\n\n')
            md.append(f'**参数**: {", ".join(combo["parameters"])}\n\n')
            md.append(f'**用途**: {combo["usage"]}\n\n')
        
        # 表情配置
        md.append('## 😊 表情配置\n\n')
        for expr in self.generate_expression_configs():
            md.append(f'### {expr.name}\n\n')
            md.append('```json\n')
            md.append(json.dumps(expr.parameters, indent=2))
            md.append('\n```\n\n')
        
        return ''.join(md)


# 使用示例
if __name__ == "__main__":
    designer = EnhancedParameterDesigner()
    
    # 列出所有模板
    print("可用的参数模板:")
    for template in designer.templates:
        print(f"  - {template.name}: {template.description}")
    print()
    
    # 推荐模板
    features = ['长发角色', 'VTuber']
    recommended = designer.get_recommended_template(features)
    print(f"推荐模板: {recommended.name}")
    print()
    
    # 生成报告
    print(designer.generate_report_markdown('标准模板'))
