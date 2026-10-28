# 源码仓库说明

本目录下原说明保留其日期及当时状态，当前仓库范围与链路以[根README](../README.md)为准。运行数据、原始聊天、VM与第三方安装器未上传，历史证据链接需在原部署机器查看。不要把历史地址或资源ID用于自己的部署。

---

# P06/P07 数据分析

2026-10-20已连接真实课程数据库并完成命令行全量批次[preset_deploy_20261020](output/preset_deploy_20261020/manifest.json)：84,252条原始记录、81,636条保留记录，LDA、SnowNLP和五CSV已生成；计数及哈希核验见[质量报告](output/preset_deploy_20261020/quality_review.md)。

当前状态为“计算已完成，人工与业务验收未完成”。还需模型辅助清洗记录、主题与情感人工复核、Notebook实际执行和五图、魔方字段映射与真实上传。项目分析Notebook目前没有执行输出；已通过的环境Notebook与该Notebook是两个文件。P06/P07/CP5尚未整体通过，详见[项目当前进度](../项目实施日志.md)。

## 连接与来源

在项目根目录 `.env` 中由本机配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_READ_USER`、`MYSQL_READ_PASSWORD`。使用单独的只读账号，仅授予指定源表 SELECT 权限；程序不复用 `MYSQL_USER`/`MYSQL_PASSWORD` 写入账号，也不创建用户或修改数据库。不要把真实凭据写进 Notebook 或日志。

默认 `preset` 固定读取 `SELECT id, time, records FROM chat.records ORDER BY id`。SQL原件与实际导入后核验均为这三列，共84,252行，其中2,616行records为SQL NULL，时间范围2023-03-01至2024-08-28。不要沿用HTML示例的六列或82,000行。

预置文件是历史聊天资料，尚不能据项目场景认定它一定属于热水器业务。主题只输出实际关键词与0/1/2编号，待查看本批代表文本后人工命名，不预填业务主题或声称K=3最优。

只有显式选择 `self_collected` 才固定读取 `wechat.new_chathistory` 的 `id,time,records`；该表必须事先从在线用户提问整理而来，不混入助手回答。两个来源分别建批次，不联合查询、不合并统计；代码没有任意 SQL 输入接口。

## 顺序与规则

1. 单次 SELECT 读取源表，在 `data/private/analysis/<来源>_<批次>/source_snapshot.jsonl` 保存本机快照及 SHA256。该目录已由项目 `.gitignore` 的 `data/private/` 排除，不能上传原始聊天。
2. pandas 顺序排除 NULL 文本、规范空白后仍为空的文本、无法解析的日期；逐步计数，保证原始行数＝保留行数＋排除行数。只合并连续空白、去首尾空白；不去重、不按长度、情绪或模型判断删除聊天。重复文本/ID和缺失ID只报告，`source_row` 保留快照行号。
3. jieba 关闭 HMM，生成 tokens 与 processed_text；词语转小写，排除停用词及无任何字母/汉字的纯数字和标点 token。不删除原行；否定词禁止加入停用词表。停用词为本项目自建，哈希入证据。
4. gensim Dictionary → BOW → LdaModel，K=3、random_state=42、passes=10、iterations=100，整批只拟合一个模型。每个有效文档只分配一个概率最高的主主题，概率相同取较小编号；无有效 token 的行不强行分主题。没有可用语料时停止，不生成伪模型。K=3 为课程示范配置，不代表已经证明最优。
5. SnowNLP 使用清洗后的完整语义文本，**不用 processed_text 或主题名评分**。`score < 0.4` 为负面，`0.4 <= score <= 0.6` 为中性，`score > 0.6` 为正面；0.4/0.6 是本项目暂定参数，非课程原文。评分失败单列，不硬塞中性。得分不是已校准概率或客服满意率，需人工检查否定、反讽与热水器领域表达。
6. 五份 CSV 与 gensim 模型写入 `analysis/output/<来源>_<批次>/`；逐条主主题、情感与原始映射只保存在上述 private 目录的 `record_results.jsonl`。公共 manifest 只含来源、批次、计数、参数、版本、主题关键词、文件哈希与待验收项，没有聊天正文或凭据。

## 暂定 CSV 契约

以下表头为项目补充契约，**待课程 Notebook/魔方实际字段映射核对后定版**。五个文件均为 `utf-8-sig`，count 为整数；不直接写 C:\ 根目录，以批次目录作为可追溯备份，上传时选择同一批次五文件。

| 文件 | 表头 | 统计口径 |
| --- | --- | --- |
| 聊天密度小时分布.csv | hour,count | 0–23 小时补零；总和等于清洗保留行数。 |
| 聊天密度月分布.csv | month,count | YYYY-MM，最早至最晚月份连续补零；总和等于保留行数。 |
| 聊天词语频次数据.csv | word,count | 分词出现频次 TOP100；同频按词排序，不能用此总和充当消息数。 |
| 月度主题趋势数据.csv | month,topic,count | 每月三个编号0/1/2补零；总和只等于成功分主主题的行数。编号尚非人工主题名称。 |
| 情感分布.csv | sentiment,count | 负面/中性/正面三行；分母为成功评分行数，失败数另见 manifest。 |

## 实际运行

先在项目根目录用已安装的 Python 3.10.9 环境运行自测：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_analysis
```

真实数据库、只读账号与源表核验完成后再运行：

```powershell
.\.venv\Scripts\python.exe analysis/run_analysis.py --source-mode preset --batch-id 20261019_run01
# 自采入口：--source-mode self_collected --batch-id 20261019_self01
```

批次仅允许 1–64 个英文字母、数字、下划线或短横线；已经存在的批次拒绝覆盖。也可以省略 `--batch-id` 自动使用北京时间时间戳。数据、参数、停用词或来源变化后新建批次。不要把小样本测试输出上传魔方或当作真实项目成果。

Notebook 位于 `notebooks/项目数据分析.ipynb`，选择项目 `.venv` 内核，从上往下执行：读库→快照→清洗→分词→LDA→情感→导出→本地五图。Notebook 没有预填运行结果。它调用同一套函数，避免与命令行产生两套规则。

成功生成文件后状态仍是 `generated_pending_manual_review`；有评分失败时明确标记 `generated_with_scoring_failures_pending_review`，失败样本不进入情感三类分母。还需 T11 模型辅助规则复核（建议/人工决定/采用规则）、异常样本复核、主题命名及K合理性检查、情感人工标注核对、实际魔方映射与上传验证；当前没有请求外部模型，也没有预填准确率。原始快照和逐条结果仅在本机保留，聚合 CSV/模型也应在上传前检查内容。

官方方法参考：[gensim LdaModel](https://radimrehurek.com/gensim/models/ldamodel.html)、[SnowNLP](https://github.com/isnowfy/snownlp)、[pandas 日期转换](https://pandas.pydata.org/pandas-docs/version/2.2.3/reference/api/pandas.to_datetime.html)。
