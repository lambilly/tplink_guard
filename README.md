# TPLink Guard — Home Assistant 自定义集成

周期登录 TP-Link 路由器，读取在线终端表并与本地名单比对：

- **不在白名单中的终端一律视为陌生**
- 白名单内还可以单独标记「关注终端」，用于重点盯防
- 关注终端名单通过实体属性暴露，自动化 / Node-RED 可直接读取，无需在脚本里硬编码设备信息

> 本项目为社区作品，与 TP-Link 官方无关。仅在本机轮询路由器，不向任何外部服务发送数据。

## 功能

- 使用路由器原生 SLP JSON 接口（`stok` 登录 + `/ds` 数据接口），账号 / 密码 / 端口可配置
- 刷新周期可配置（默认 60 秒，最小 5 秒）
- 读取 `host_management/host_info` 在线终端表；另读取 `dhcpd/dhcp_static` 统计静态分配数
- **白名单 TXT**：每行格式 `MAC # 字段1,字段2,...`（逗号区隔，IP 不加括号），不使用引号
- **手机名单 TXT**（`lists/phone.txt`，原关注名单）：每行格式与白名单相同，但只有同时存在于白名单中的设备才算关注终端
- 手机名单备注字段为 `姓名,昵称,设备名`，解析后随实体属性 `watchlist` 输出
- **监控终端名单 TXT**（`lists/monitored.txt`）：每行 `MAC,实体名,显示名`，第二字段生成实体 ID（`device_tracker.tplink_guard_<实体名>`），第三字段为中文显示名（可省略）
- 支持按 MAC、IP 或两者匹配；名单文件变化时自动重载，也可调用服务 `tplink_guard.reload_whitelist`
- 自带本地 LOGO（`custom_components/tplink_guard/brand/`）
- 仅生成聚合实体，不逐台创建设备传感器

## 前置要求

- 一台可通过 Web 管理接口登录的 TP-Link 路由器，管理端口可配置（默认 `80`；部分机型使用其它端口，请以实际设备为准）
- Home Assistant（需支持自定义集成与配置向导）

## 安装

1. 把 `custom_components/tplink_guard/` 整个目录复制到 HA 配置目录下的 `custom_components/`。
2. 把 `examples/` 下的示例文件复制到 **`custom_components/tplink_guard/lists/`**（随集成目录一起），并改名为：
   - `whitelist.txt`（白名单）
   - `phone.txt`（手机名单）
   - `monitored.txt`（监控终端名单，不需要逐台实体可留空）
   然后按你自己的终端填写。
3. 重启 Home Assistant。
4. 在 HA 中进入「设置 → 设备与服务 → 添加集成」，搜索 `TPLink Guard`，按提示填写：
   - 主机地址、管理端口、用户名、密码
   - 刷新周期（秒）
   - 白名单 / 手机名单 / 监控终端名单文件路径（默认 `custom_components/tplink_guard/lists/` 下的 `whitelist.txt` / `phone.txt` / `monitored.txt`）
   - 匹配方式（建议 `both`）

> 密码只保存在 HA 的 `.storage/core.config_entries` 中，不会写入本项目的日志或文件。
> **升级提示**：名单存放在集成目录内，更新集成时请只覆盖代码文件，勿删除 `lists/` 子目录；旧版本把名单放在 `/config` 根目录的路径依然会被自动重定向到 `lists/`，无需手动修改。

## 实体

实体名称与 ID 全部使用英文，术语统一为 `terminal` / `phone` / `monitored`。

| 实体 ID | 英文名 | 含义 |
|---|---|---|
| `sensor.tplink_guard_online_terminal_count` | Online Terminal Count | 当前在线终端数 |
| `sensor.tplink_guard_whitelist_online_count` | Whitelist Online Count | 白名单内在线数 |
| `sensor.tplink_guard_unknown_terminal_count` | Unknown Terminal Count | 陌生在线终端数 |
| `sensor.tplink_guard_unknown_terminal_ip` | Unknown Terminal IP | 陌生终端 IP 列表 |
| `sensor.tplink_guard_whitelist_terminal_count` | Whitelist Terminal Count | 白名单终端数 |
| `sensor.tplink_guard_static_ip_count` | Static IP Count | 静态分配终端数（属性含启用数与固件上限 80） |
| `sensor.tplink_guard_phone_online_count` | Phone Online Count | 手机名单终端在线数 |
| `sensor.tplink_guard_phone_offline_count` | Phone Offline Count | 手机名单终端离线数 |
| `sensor.tplink_guard_phone_online_ip` | Phone Online IP | 手机名单在线 IP 列表 |
| `sensor.tplink_guard_guest_visit_count` | Guest Visit Count | 来访统计（state=在家人数，单位「人」，含 9 个 guest_* 属性） |
| `sensor.tplink_guard_monitored_online_count` | Monitored Online Count | 监控终端名单在线数 |
| `sensor.tplink_guard_monitored_offline_count` | Monitored Offline Count | 监控终端名单离线数 |
| `sensor.tplink_guard_monitored_terminal_count` | Monitored Terminal Count | 监控终端名单总台数 |
| `binary_sensor.tplink_guard_unknown_terminal_present` | Unknown Terminal Present | 发现陌生终端 / 无陌生终端 |
| `binary_sensor.tplink_guard_phone_online` | Phone Online | 在线 / 离线 |
| `device_tracker.<实体ID>` | <实体ID> | 监控终端在线状态（home / not_home），由监控终端名单逐台生成 |

> **升级说明**：HA 2026.11 强制按「集成名前缀 + 实体名」生成全新安装的实体 ID（`_attr_suggested_object_id` 被忽略），因此新安装的实体默认带 `tplink_guard_` 前缀。所有实体统一使用 `tplink_guard_` 前缀（Node-RED 流已按带前缀 ID 引用）。已注册的实体 ID 不会随版本自动改变；需要自定义 ID（如 `device_tracker.hp_imac`）可在「设置 → 设备与服务 → 实体」中改名，集成不会覆盖手动改名。

`sensor.tplink_guard_guest_visit_count` 为来访统计实体（state 与 9 个属性 `guest_all_count / guest_all_list / guest_home_count / guest_home_list / guest_nick_list / guest_out_count / guest_out_list / guest_detail / update_time` 完全一致），由集成直接生成；确认新实体正常后即可停用旧的 Node-RED 流。注意：全新安装时该实体 ID 会按名称生成为 `sensor.guest_visit_count`，如需与旧流一致的 `sensor.guest_visit_count`，可在实体注册表中手动改名。

两个 binary_sensor 的状态文字通过实体翻译显示为「发现陌生终端 / 无陌生终端」与「在线 / 离线」，避免默认「开启 / 关闭」的歧义。

单台设备的在线 / 离线明细不单独生成实体，可在聚合实体的属性 `devices` 中查看具体 IP、MAC 与主机名。

## 名单格式（无引号，逗号区隔）

两个名单每行格式统一为 `MAC # 字段1,字段2,...`，字段用逗号区隔，IP 不加括号。

白名单示例（字段：名称,IP，IP 可选）：

```
# 这是注释
4C-C6-4C-EC-0C-3B # 网关,192.168.1.6
20-BD-1D-D7-85-57 # 台式机,192.168.1.10
AA-BB-CC-DD-EE-01 # 只写名称也可以
```

手机名单示例（字段：姓名,昵称,设备名）：

```
AA-BB-CC-DD-EE-01 # 张三,三哥,Phone-A
```

不在白名单中的设备即使写入手机名单也不会生效。名单改完会自动重载，无需重启 HA。

监控终端名单示例（字段：MAC,实体名,显示名）：

```
20-BD-1D-D7-85-57, hui_pu_dian_nao, 惠普电脑
AA-BB-CC-DD-EE-01, dev_a, 设备A
AA-BB-CC-DD-EE-02
```

- 第二字段生成**实体 ID**：`hui_pu_dian_nao` → `device_tracker.tplink_guard_hui_pu_dian_nao`；可带或不带 `device_tracker.` 前缀填写
- 第三字段是**前端显示名**（可用中文），与实体 ID 相互独立——集成会同时钉住 ID 与显示名，HA 2026.11 不会重生成
- 省略 `显示名` 时显示实体名；省略 `实体名` 时按路由器主机名自动生成
- 名单内每台终端生成一个 `device_tracker` 实体：在线为 `home`，离线为 `not_home`，状态未变化时持续时间计时不会重置，属性含 `mac / ip / hostname / last_update`

## 给自动化 / Node-RED 使用

`sensor.tplink_guard_phone_online_ip` 的 `watchlist` 属性是一个数组，包含**完整的关注名单**（含离线设备）：

```json
[
  { "mac": "AABBCCDDEE01", "ip": "", "name": "张三", "nickname": "三哥", "device": "Phone-A" }
]
```

`binary_sensor.tplink_guard_phone_online` 与 `sensor.tplink_guard_phone_online_ip` 的 `devices` 属性则是**当前在线**的关注终端。

因此自动化 / Node-RED 只需读取这两个实体属性即可判断「哪些关注的人在家」，**不需要在脚本里硬编码 MAC、姓名或昵称**，也不需要额外读取名单文件。增删家人时只改手机名单 TXT 一行即可。

`examples/automations.yaml` 提供了陌生终端告警与关注终端上线提醒的示例。

## 目录

```
custom_components/tplink_guard/   # HA 自定义集成源码（含 brand/ 本地 LOGO）
examples/                         # 名单示例与自动化示例
  tplink_guard_whitelist.example.txt
  tplink_guard_phone.example.txt
  tplink_guard_monitored.example.txt
  automations.yaml
LICENSE                           # MIT
```

真实名单文件（`custom_components/tplink_guard/lists/` 下的 `whitelist.txt` / `phone.txt` / `monitored.txt`）含个人网络信息，已通过 `.gitignore` 排除，不会进入仓库。

## 免责声明

本项目的路由器登录密码编码算法来自设备前端脚本，属于逆向实现，可能因固件版本不同而失效。使用风险自负。
