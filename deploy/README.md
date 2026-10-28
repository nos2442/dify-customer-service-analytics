# 源码仓库说明

本目录下原说明保留其日期及当时状态，当前仓库范围与链路以[根README](../README.md)为准。运行数据、原始聊天、VM与第三方安装器未上传，历史证据链接需在原部署机器查看。不要把历史地址或资源ID用于自己的部署。

---

# P2 只读环境检查

2026-10-20实际部署已更新为8核、10GB内存、桥接静态192.168.18.176；MySQL/Dify/Xinference安装、数据与冷启动验证见[运行与恢复](../docs/运行与恢复.md)。root按用户要求支持仅原Windows主机来源的密码SSH；六类实际口令仅保存在本机私有`.env.login-notes.md`。下方新机准备参数保留为最初准备时的说明，不能重复建机或据此覆盖现有部署。

## 新虚拟机准备

`prepare_vm.py`仅在全新`vm/openEuler-dify`目录创建4核、6GB内存、100GB薄磁盘及OEMDRV应答介质，不会开机，不会覆盖旧VM。该目录已创建，不能重复执行。原课程参考32GB内存，本机按实际资源采用6GB并安排模型分时运行。

应答文件只允许新VM的`sda`参与分区；主系统盘与旧Hadoop磁盘完全分开。SSH使用本地项目密钥，默认禁止密码登录与root远程登录；私钥留在已忽略的VM目录，不写入日志。正式ISO必须先匹配官方SHA256，才能启动安装。部署后先核对实际系统、IP、SSH和sudo，再继续安装Docker/MySQL。

## 安装后的只读检查

在 openEuler 的项目目录运行，无需 sudo 或执行权限：

```sh
sh -n deploy/check_p2.sh
sh deploy/check_p2.sh > p2_preflight.txt
```

检查系统、资源、IP、监听端口、Docker/Compose 和现有容器。所有结果及错误均写入标准输出；缺少命令标记为 `MISSING`，权限不足或服务不可用标记为 `UNAVAILABLE`，仍继续检查。退出码 0 仅表示检查结束，不代表 CP1/CP2 通过。证据包含主机名与 IP，仅在项目组内保存。

脚本不安装软件、不提权、不修改配置、不启停服务，也不输出环境变量或完整容器 inspect。

复用时保留既有 Compose 目录、`.env`、数据挂载与镜像版本；先核对端口和实际版本，不覆盖配置、不清空数据、不执行降级迁移。课程 MySQL 独立于 Dify 内部 PostgreSQL；不使用 `root/root` 作为项目默认凭据。

Dify 1.8.0 官方依据：[Compose](https://github.com/langgenius/dify/blob/1.8.0/docker/docker-compose.yaml)、[环境模板](https://github.com/langgenius/dify/blob/1.8.0/docker/.env.example)、[部署说明](https://github.com/langgenius/dify/blob/1.8.0/docker/README.md)。新部署保留完整 `docker/` 目录；默认宿主映射 80、443 和插件调试 5003，HTTPS 默认未启用，不因端口已映射就视为可用或允许公网开放。
