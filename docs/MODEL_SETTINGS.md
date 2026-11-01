# 模型配置持久化（v0.7.2）

## 用户行为

设置→配置模型，填写地址/模型/Key，勾选启用及“记住密钥和启用状态”，点击保存。记住默认勾选，启用仍由用户选择。以后重启或升级时恢复；只是读取本机配置，不测试连接、不请求模型。

取消记住后仅本次运行有效，原已保存密钥会从配置移除；下次不会自动启用，包括免密本地接口。清除密钥立即删除配置中的密文、清空当前输入/内存引用并停用，保留地址和模型。API Key输入遮罩显示；修改地址清空旧Key输入，更换模型名可复用同一接口的Key。

v0.7.1及更早版本只存地址/模型，已关闭进程的内存Key不能恢复。升级后提示重新填写保存一次，不声称迁移了从未持久化的数据。账单数据和安装AppId不变。

## 存储和失败处理

`finance/model_settings.py`使用标准库ctypes调用Windows DPAPI，当前用户范围，固定应用entropy与版本号/安装路径无关；不新增依赖。资料：[CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)、[CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)。没有使用LOCAL_MACHINE标志，没有明文降级。

数据文件沿用 `%LOCALAPPDATA%/ShiguangLedger/model-settings.json`（测试可通过FINANCE_DESKTOP_DATA_DIR隔离）。version=2，包含base_url、model、enabled、remember_key、requires_key及可选encrypted_api_key；只有DPAPI密文编码为base64，不能把base64本身称为加密。密文载荷也绑定接口地址；文件地址被修改时不恢复旧Key到另一主机。

原子写临时文件→flush/fsync→replace同一配置文件，避免地址和密钥跨文件保存不一致。加密或替换失败时旧配置保持。损坏、无法解密、跨账户复制或未来配置版本时停用并提示，读取时保留原文件。只支持Windows持久密钥，其他系统取消记住可仅本次使用。

这是静态文件保护，不能承诺抵御当前用户权限下的恶意进程；切换电脑/用户可能需要重填。用户清除是移除活动配置，不承诺SSD历史扇区/系统备份安全擦除。密钥不进入账本、原始行、日志、截图或公开仓库；测试仅用虚构值。

## 验证

- `tests/test_model_settings.py`：旧配置/损坏、跨实例恢复且无网络、明文检查、仅本次/免密、停用保留Key、清除、地址篡改、加密/原子写失败保留、损坏密文；Windows真实DPAPI跨进程与完整性检查。
- `scripts/verify_model_persistence_ui.py`：七个独立进程阶段验证保存→重启恢复→地址变化清空→清除→重启停用→仅本次→重启未启用；全程阻断模型网络请求。
- `scripts/verify_packaged_model_settings.py`：虚构DPAPI配置，真实打包EXE连续两次启动/自检恢复，磁盘配置不变。不能把源代码测试等同于安装认证。
- 修改后的模型弹窗在200%模拟下可滚动，底部按钮不裁切；原生150%亦检查。完整回归结果与打包验证完成后记入VALIDATION.md。
