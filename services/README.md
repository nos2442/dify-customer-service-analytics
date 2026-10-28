# 服务运行说明

当前微信个人号使用direct被动回复；常见身份问候和安全提示本地回答，明确型号参数由商品MD读取，其他咨询调用Dify快速工作流。复杂问题仍有模型超时风险，账号无权限时无法在超时后自动补发客服消息。

运行入口、配置示例与八节点链路见根README。`.env`需填写WECHAT_TOKEN、DIFY_BASE_URL、DIFY_WORKFLOW_API_KEY，推荐WECHAT_REPLY_MODE=direct、WECHAT_MESSAGE_MODE=plain。AppID/AppSecret仅在使用对应接口且账号已获权限时配置。

`mysql_server.py`读取MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD和MCP_HOST/MCP_PORT，数据库结构用deploy/init_wechat.sql准备；只接受代码中固定INSERT模板及JSON字符串参数，不开放任意SQL。

`customer_assistant_workflow.yml`为原六节点RAG模板，需要在自己的Dify选择真实知识库、模型和MCP提供方。`web_crawler_workflow.yml`、`web_crawler_sync_workflow.yml`为网页抓取/同步模板；导出中的secret为空，上传副本中的内网API地址及数据集ID已替换占位值。当前快速客服由scripts/configure_wechat_fast.py构图，不把旧模板当作现网导出。

本仓库不提供实际微信公众号凭据、Dify运行密钥或长期有效回调域名。scripts/start_wechat_background.ps1等运维工具依赖原本机版本、路径和审核过的cloudflared安装器；未上传安装器，使用前按自己的环境处理，避免覆盖既有资源。
