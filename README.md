# JianYing-AI-Pro (剪映 AI 全自动生产力工具箱)

基于大模型视觉能力与本地 RPA 结合的剪映草稿自动化处理工具。

## 功能特性
1. **自动导出**: 自动识别草稿坐标并进行自动化渲染导出。
2. **字幕提取**: 提取 `draft_content.json` 内的字幕并删除原字幕轨道（无字幕版制作）。
3. **智能重命名**: 使用 AI 识别规律，一键批量重命名导出的视频。
4. **云端强控 Kill-Switch**: 随时通过 Gist 控制软件使用权限与自毁。

## 安装依赖
```bash
pip install -r requirements.txt
```

## 运行
```bash
python main.py
```
