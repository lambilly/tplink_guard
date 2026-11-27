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
- **白名单 TXT**：每行格式 `MAC, IP, 名称`，IP 字段可选（动态 / 无 IP 留空），第 3 列为设备名称；不使用引号
- **客人手机名单 TXT**（`lists/guestphone.txt`，原手机名单 / 关注名单）：格式为 `MAC, IP, 姓名, 昵称`，但只有同时存在于白名单中的设备才算客人手机终端
- 名称字段为 `姓名,昵称`（第 3、4 字段），解析后用于来访统计的在家 / 外出列表显示；统计并入 `guest_visit_count`，不再单独生成手机在线数实体
- **设备终端名单 TXT**（`lists/device.txt`）：每行 `MAC,IP,显示名,实体名`，第二列 IP 预留暂空（`MAC,,显示名,实体名`），第三字段为中文显示名，第四字段生成实体 ID（`device_tracker.tplink_guard_<实体名>`）
- **AP 名单 TXT**（`lists/ap.txt`）：每行 `MAC, IP, 名称`，第 2 列 IP 直接探测 AP 是否在线（AC 管理的 AP 不在路由器 `host_info` 中），第 3 列名称用于各 `*_list` 属性显示；IP 为空（`MAC,,名称`）的行会跳过。生成 AP Online Count 一个实体，总数 / 离线数以属性提供
- **分组在线名单 TXT**（`lists/gateway.txt` / `lists/asr.txt` / `lists/server.txt` / `lists/router.txt`）：统一格式 `MAC,IP,中文名,英文名`，IP 动态或为空时用 `,,` 跳过（如 `MAC,,中文名,英文名`）。MAC 比对 TPLINK 在线终端表，IP 额外做 LAN 探测（任一命中即在线，可修正路由器把在线设备误标 offline）。分别生成 Gateway / ASR / Server / Router Online Count，增删终端只改对应 TXT 一行，文件变化自动重载；不依赖任何 HA 实体状态或 `iphonedetect` 的 device_tracker
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
   - `guestphone.example.txt` → `guestphone.txt`（客人手机名单）
   - `device.example.txt` → `device.txt`（设备终端名单，不需要逐台实体可留空）
   - `ap.example.txt` → `ap.txt`（AP 名单，不需要 AP 监控可留空）
   - `gateway.example.txt` → `gateway.txt`（网关 / 蓝牙代理名单，不需要网关监控可留空）
   - `asr.example.txt` → `asr.txt`（ASR 语音终端名单，不需要 ASR 监控可留空）
   - `server.example.txt` → `server.txt`（服务器 / 主机名单，不需要服务器监控可留空）
   - `router.example.txt` → `router.txt`（网络基础设施名单，不需要路由器监控可留空）
   然后按你自己的终端填写。
3. 重启 Home Assistant。
4. 在 HA 中进入「设置 → 设备与服务 → 添加集成」，搜索 `TPLink Guard`，按提示填写：
   - 主机地址、管理端口、用户名、密码
   - 刷新周期（秒）
   - 白名单 / 客人手机名单 / 设备终端名单 / AP 名单 / 网关 / ASR / 服务器 / 路由器名单文件路径（默认 `custom_components/tplink_guard/lists/` 下的 `whitelist.txt` / `guestphone.txt` / `device.txt` / `ap.txt` / `gateway.txt` / `asr.txt` / `server.txt` / `router.txt`）
   - 匹配方式（建议 `both`）

> 密码只保存在 HA 的 `.storage/core.config_entries` 中，不会写入本项目的日志或文件。
> **升级提示**：名单存放在集成目录内，更新集成时请只覆盖代码文件，勿删除 `lists/` 子目录；旧版本把名单放在 `/config` 根目录的路径依然会被自动重定向到 `lists/`，无需手动修改。

## 实体

实体名称与 ID 全部使用英文，术语统一为 `terminal` / `guest phone` / `device`。

| 实体 ID | 英文名 | 含义 |
|---|---|---|
| `sensor.tplink_guard_all_online_count` | All Online Count | 当前在线终端总数（单位「个」）；白名单 / 静态分配 / 陌生终端三组数据作为属性挂在此实体上：`whitelist_*`（总数、在线数、离线数与三组列表）、`static_ip_count / static_ip_enabled / static_ip_limit`、`unknown_online_count / unknown_online_list` |
| `sensor.tplink_guard_guest_visit_count` | Guest Visit Count | 客人来访统计：state = 在家人数（单位「人」），手机名单的在线 / 离线统计已并入此实体，9 个属性 `guest_all_count / guest_all_list / guest_home_count / guest_home_list / guest_nick_list / guest_out_count / guest_out_list / guest_detail / update_time` |
| `sensor.tplink_guard_device_online_count` | Device Online Count | 设备终端名单在线数（单位「个」）；总数 / 离线数与列表作为属性 `device_all_count` / `device_offline_count` / `device_all_list` / `device_online_list` / `device_offline_list` 提供 |
| `sensor.tplink_guard_firmware_model` | Firmware Model | 路由设备型号（如 TL-R498GPM-AC 3.0，属性含硬件 / 固件版本、设备 MAC）；内部数据键仍为 `router_model`，实体 ID 与名称已改为 Firmware Model |
| `sensor.tplink_guard_ap_online_count` | AP Online Count | 当前在线 AP 数；总数与离线数作为属性 `ap_all_count` / `ap_offline_count` 提供 |
| `sensor.tplink_guard_gateway_online_count` | Gateway Online Count | 网关 / 蓝牙代理在线数（兼容旧 NR 实体的 7 个属性与单位「台」） |
| `sensor.tplink_guard_asr_online_count` | ASR Online Count | ASR 语音终端在线数（兼容旧 NR 实体的 7 个属性与单位「个」） |
| `sensor.tplink_guard_server_online_count` | Server Online Count | 服务器 / 主机在线数（兼容旧 NR 实体的 7 个属性与单位「台」） |
| `sensor.tplink_guard_router_online_count` | Router Online Count | 网络基础设施在线数（兼容旧 NR 实体的 7 个属性与单位「台」） |
| `device_tracker.<实体ID>` | <实体ID> | 设备终端在线状态（home / not_home），由设备终端名单逐台生成 |

所有分组按「每组一个 online count 实体」组织：原来的离线数、总数、IP 列表等实体已取消，计数与列表改为属性挂在主实体上。Whitelist / Static IP / Unknown 三组并入 `all_online_count`；手机名单并入 `guest_visit_count`，`sensor.tplink_guard_phone_online_count` 实体与 `phone_*` 属性全部移除。各实体只保留计数 / 列表类属性，不再输出 `devices` 数组、`watchlist`、`phone_detail`、`phone_count`、`phone_online_ip`、`monitored_devices`、`monitored_terminal_count`、`whitelist_count`、`whitelist_terminal_count` 等大体积或重复属性；两个 binary_sensor 也已删除。

> **升级说明**：HA 2026.11 强制按「集成名前缀 + 实体名」生成全新安装的实体 ID（`_attr_suggested_object_id` 被忽略），因此新安装的实体默认带 `tplink_guard_` 前缀。所有实体统一使用 `tplink_guard_` 前缀（Node-RED 流已按带前缀 ID 引用）。已注册的实体 ID 不会随版本自动改变；需要自定义 ID（如 `device_tracker.hp_imac`）可在「设置 → 设备与服务 → 实体」中改名，集成不会覆盖手动改名。

`sensor.tplink_guard_guest_visit_count` 为来访统计实体，state = 在家人数（单位「人」），9 个属性 `guest_all_count / guest_all_list / guest_home_count / guest_home_list / guest_nick_list / guest_out_count / guest_out_list / guest_detail / update_time` 与旧 Node-RED 流输出一致。原 `sensor.tplink_guard_phone_online_count` 的总数 / 在线数 / 离线数及三组列表均在这 9 项中重复，故已删除该实体，不再输出 `phone_*` 属性；确认新实体正常后即可停用旧的 Node-RED 流。

单台设备的在线 / 离线明细不单独生成实体；设备终端名单可通过 `device_tracker.tplink_guard_<实体名>` 查看，终端总览可读 `all_online_count` 的 `whitelist_*` / `unknown_*` 属性。

## 名单格式（无引号，逗号区隔）

白名单格式为 `MAC, IP, 名称`（IP 可选），客人手机名单格式为 `MAC, IP, 姓名, 昵称`；字段用逗号区隔，IP 不加括号。第 2 列均为 IP，名称取后续字段，空 IP 用 `,,` 留空。旧格式 `MAC, IP, #名称` 仍可读取。

白名单示例（格式 `MAC, IP, 名称`，IP 可选）：

```
# 这是注释
4C-C6-4C-EC-0C-3B, 192.168.1.6, 网关
20-BD-1D-D7-85-57, 192.168.1.10, 台式机
AA-BB-CC-DD-EE-01,, 只写名称也可以
```

客人手机名单示例（字段：MAC, IP, 姓名, 昵称）：

```
AA-BB-CC-DD-EE-01, 192.168.1.140, 张三, 三哥
```

不在白名单中的设备即使写入客人手机名单也不会生效。名单改完会自动重载，无需重启 HA。

设备终端名单示例（格式 `MAC,IP,显示名,实体名`，IP 列暂留空）：

```
20-BD-1D-D7-85-57,, 惠普电脑, hui_pu_dian_nao
AA-BB-CC-DD-EE-01,, 设备A, dev_a
```

- 第二字段为预留的 **IP 列**（动态 / 无 IP 留空，写成 `MAC,,显示名,实体名`）
- 第三字段是**前端显示名**（可用中文），第四字段生成**实体 ID**：`hui_pu_dian_nao` → `device_tracker.tplink_guard_hui_pu_dian_nao`；可带或不带 `device_tracker.` 前缀填写
- 显示名与实体 ID 相互独立——集成会同时钉住 ID 与显示名，HA 2026.11 不会重生成
- 省略 `实体名` 时按路由器主机名自动生成
- 名单内每台终端生成一个 `device_tracker` 实体：在线为 `home`，离线为 `not_home`，状态未变化时持续时间计时不会重置，属性含 `mac / ip / hostname / last_update`

AP 名单示例（字段：`MAC, IP, 名称`）：

```
24-5A-5F-E8-81-49, 192.168.1.11, 客厅AP
EC-60-73-35-89-E7, 192.168.1.12, 主卧AP
3C-6A-48-73-DB-B0, 192.168.1.13, 次卧AP
```

- AP 由路由器 AC 管理，不会出现在 `host_info` 在线终端表里，因此集成直接对名单中的 IP 做在线探测（先 ICMP，失败时尝试 TCP 80/443/22）
- 探测与路由器 Web 登录会话无关：即使集成处于「礼让模式」（用户正在登录路由器 Web 界面），AP 状态仍会正常刷新
- 只保留 `sensor.tplink_guard_ap_online_count` 一个实体，状态为在线 AP 数、单位「个」；总数与离线数作为属性提供：`ap_all_count / ap_all_list / ap_online_count / ap_online_list / ap_offline_count / ap_offline_list / update_time`（列表以 `｜` 分隔，离线为空时显示「无」），不再输出 `aps` 数组
- 名称可省略（省略时用 IP 显示）；`#` 后为注释；名单变化自动重载，也可调用 `tplink_guard.reload_whitelist`

网关 / ASR / 服务器 / 路由器共用同一套名单格式 `MAC,IP,中文名,英文名`，文件分别位于 `lists/gateway.txt`、`lists/asr.txt`、`lists/server.txt`、`lists/router.txt`：

```
# gateway.txt
1C-EA-AC-35-A3-89, 192.168.1.4, 青萍网关, qingping_gateway
18-C2-3C-1C-5B-5F, 192.168.1.7, 多模网关, duomo_gateway
F4-CF-A2-E5-D5-B1,, 复刻小乐, fuke_xiaole        # IP 动态/空，用 ,, 留空
# router.txt
, 192.168.1.1, 主路由, main_router               # 无 MAC、只有 IP
94-83-C4-53-EC-0C, 192.168.1.2, 旁路由, istoreos
```

判定规则：
- **MAC**（12 位十六进制）：比对 TPLINK 在线终端表，设备换 IP 也能正确识别
- **IP**（IPv4）：直接做 LAN 探测（先 ICMP、失败回退 TCP 80/443/22）。IP 列非空时作为补充判定，可修正路由器把在线设备误标 `state=offline` 的情况（如多模网关）；MAC 命中或 IP 探测通，任一成立即在线
- 第一列本身是 IPv4 的行（如主路由 / 交换机）直接探测该 IP，因为它们不在自己的终端表
- IP 动态或为空时用 `,,` 留空，此时仅靠 MAC 比对；名单只接受 MAC 或 IP，不读取 HA 实体状态。名单变化自动重载，也可调用 `tplink_guard.reload_whitelist`
- 四个在线数实体各复刻旧 NR 实体的 7 个属性（`<组名>_all_count / _all_list / _online_count / _online_list / _offline_count / _offline_list / update_time`），不再输出 `<组名>_devices` 明细
- 单位为「台」「个」「台」「台」，与旧 NR 实体一致；新增设备只需在对应 TXT 增加一行，无需改代码或重启

## 给自动化 / Node-RED 使用

客人手机终端请使用 `sensor.tplink_guard_guest_visit_count`：state 为在家人数，`guest_all_list / guest_home_list / guest_out_list` 分别是全部 / 在家 / 外出的姓名列表，`guest_nick_list` 为在家人员昵称列表，`guest_detail` 为来访文字说明。原手机在线数实体及其 `phone_*` 属性已删除，统计项均可在该实体读取。

陌生终端请使用 `sensor.tplink_guard_all_online_count` 的属性：`unknown_online_count` 为陌生在线终端数，`unknown_online_list` 为这些在线陌生终端的 IP 字符串（多个以逗号分隔，无为 `none`）。两个 binary_sensor（`unknown_terminal_present`、`phone_online`）已删除，改用计数实体的对应属性触发即可。

`examples/automations.yaml` 提供了陌生终端告警与关注终端上线提醒的示例。

## 目录

```
custom_components/tplink_guard/   # HA 自定义集成源码（含 brand/ 本地 LOGO）
examples/                         # 名单示例与自动化示例
  whitelist.example.txt
  guestphone.example.txt
  device.example.txt
  ap.example.txt
  gateway.example.txt
  asr.example.txt
  server.example.txt
  router.example.txt
  automations.yaml
LICENSE                           # MIT
```

真实名单文件（`custom_components/tplink_guard/lists/` 下的 `whitelist.txt` / `guestphone.txt` / `device.txt` / `ap.txt` / `gateway.txt` / `asr.txt` / `server.txt` / `router.txt`）含个人网络信息，已通过 `.gitignore` 排除，不会进入仓库。

## 免责声明

本项目的路由器登录密码编码算法来自设备前端脚本，属于逆向实现，可能因固件版本不同而失效。使用风险自负。
