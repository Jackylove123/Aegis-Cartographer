# Aegis Cartographer

移动端自动化测绘智能体 (Mobile Mapping Agent)

## 介绍

Aegis Cartographer 是一个基于 MCP 协议的移动端自动化测绘工具，支持页面指纹识别、BFS 遍历回溯、安全过滤和 Mermaid 可视化导出。

## 功能特性

- ✅ MCP 协议支持
- ✅ 页面指纹计算 (SHA256)
- ✅ BFS 遍历与智能回溯
- ✅ 安全过滤 (危险操作拦截)
- ✅ Mermaid 可视化导出
- ✅ 向量语义搜索 (TF-IDF)
- ✅ 分片存储 (Index + Nodes)

## 快速开始

```bash
# 克隆项目
git clone https://github.com/Jackylove123/Aegis-Cartographer.git

# 创建虚拟环境
python3.13 -m venv venv
source venv/bin/activate

# 安装依赖
pip install pydantic mcp numpy scikit-learn

# 启动服务
PYTHONPATH=src ./venv/bin/python -m aegis_cartographer
```

## 项目结构

```
Aegis-Cartographer/
├── src/aegis_cartographer/
│   ├── server.py        # MCP 服务器
│   ├── app_map.py      # 地图管理器
│   ├── fingerprint.py   # 页面指纹
│   ├── traversal.py    # 遍历引擎
│   ├── security.py     # 安全过滤
│   └── vector_indexer.py # 向量索引
├── aegis_output/       # 测绘输出目录
└── pyproject.toml     # 项目配置
```
