# 聊天数据源码大屏

Vue 3、Vite、ECharts 5、echarts-wordcloud 2。页面、样式、图表配置和数据转换均有源码，无需魔方账号、虚拟机或Dify服务在线。

## 在VS Code中运行

1. 用VS Code打开本目录，或双击`聊天数据大屏.code-workspace`。
2. 打开终端，运行：

```powershell
.\start.cmd
```

浏览器访问 <http://127.0.0.1:5173>。也可在VS Code“终端 → 运行任务”选择“启动聊天大屏”。按Ctrl+C停止自己启动的服务。

启动器使用CMD调用Node，不修改PowerShell执行策略。它优先使用PATH里的Node，也兼容本机已有的Codex bundled Node。新电脑请安装Node.js 24 LTS（带npm）；首次运行需要下载依赖。本机开发使用pnpm，锁定版本保存在`pnpm-lock.yaml`。也可使用标准命令：

```powershell
npm install
npm run dev
```

安装依赖后也可直接执行`node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort`。

## 五图与交互

- 月度面积图：7个月，横轴升序、纵轴从0开始。
- 小时柱状图：0—23时累计，高亮最活跃时段。
- TOP100词云：悬停查看出现次数。
- 三主题堆叠面积趋势：点击图例开关系列；完整混合倾向解释在图下。
- 情感玫瑰图：使用ECharts代码绘制，可悬停查看真实数量和占比。等角扇区半径使用计数的平方根，使面积对应计数；原始计数单独保留，不把半径值显示成聊天数。

页面支持全屏、重新读取本地数据、数据口径说明和窄屏布局。“重新读取”只重新加载JSON，不会调用模型、数据库或重新清洗原始数据。

## 真实数据与更新

当前数据为`preset_refresh_20261024_jan_jul`，2024-01-01至2024-07-28，有效42,860条。图表读取`public/data/dashboard.json`，仅包含已有五CSV的聚合数据，不含聊天原文或账号凭据。

在项目根目录执行下列命令可重新导出，导出前会核验CSV哈希、行数、主键、合法计数及四图总量。校验失败时不覆盖原数据：

```powershell
python scripts/export_source_dashboard.py
```

切换到其他已存在的课程批次：

```powershell
python scripts/export_source_dashboard.py --batch-dir analysis/output/preset_deploy_20261020
```

导出后开发页面点击“重新读取”。如果使用`dist`生产包，需要重新构建。图表测试针对当前42,860条验收基线；换批次后不能把它作为新批次验收结果。

## 文件职责

| 文件 | 内容 |
| --- | --- |
| `src/App.vue` | 页面结构、数据载入和全屏 |
| `src/chartOptions.js` | 五图配置、计数校验与概览计算 |
| `src/components/ChartPanel.vue` | ECharts初始化、尺寸变化和资源释放 |
| `src/style.css` | 大屏和手机布局 |
| `../scripts/export_source_dashboard.py` | 五CSV到JSON的校验与导出 |
| `tests/charts.test.js` | 图表数据一致性及异常输入检查 |

## 构建与验证

```powershell
.\start.cmd test
.\start.cmd build
.\start.cmd preview
```

构建产物在`dist`，生产预览地址为 <http://127.0.0.1:4173>。需要通过HTTP服务打开，不要双击`dist/index.html`。原数据导出检查位于项目根目录的`tests/check_source_dashboard_export.py`。

统计边界：预置数据是离线快照，微信新消息不会自动进图；末月未满自然月；词频不等于消息数；三主题是混合模型组；SnowNLP标签不是投诉率或满意度。人工24条复核也未用于覆盖全量情感标签。本版本替换展示层，原魔方看板及分析模型保留。
