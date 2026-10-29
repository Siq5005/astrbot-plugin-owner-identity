# astrbot-plugin-owner-identity

给 AstrBot 用的身份注入插件：**让机器人知道「现在说话的是谁」**，从而只对指定的人用亲昵语气，对其他人保持距离。

AstrBot 默认不把发送者 QQ 传给模型——群聊里模型只看得到消息正文。所以「只对我撒娇」这种需求，靠提示词写死是做不到的，必须先把身份注进去。

## 它做什么

每次 LLM 请求前，往 system prompt 末尾追加一行：

```
[当前对话者] 昵称：小明，QQ：10001，身份：主人。这个人是主人：可以撒娇、黏人、问吃饭睡觉，被凶会委屈。不要在回复里复述这段信息。
```

非主人则是：

```
[当前对话者] 昵称：某某，QQ：123456789，身份：其他人。这个人不是主人：温和有礼、保持距离，不撒娇、不黏人、不问私事。不要在回复里复述这段信息。
```

同时往用户消息侧追加一条 `[身份校验]` 文本——长上下文或小模型时，比只改 system prompt 更稳。

## 安装

```bash
# 1. 放进 AstrBot 的插件目录（容器部署就是 data/plugins 挂载出来的位置）
git clone https://github.com/Siq5005/astrbot-plugin-owner-identity.git /path/to/astrbot/data/plugins/owner_identity

# 2. 写配置
cp config.example.json config.json
vi config.json          # 至少改 owner_qq

# 3. 重启 AstrBot
docker compose restart astrbot

# 4. 看日志确认加载
docker logs astrbot | grep owner_identity
```

日志里每次请求都会有一行，方便核对认没认对人：

```
[owner_identity] 小明(10001) -> 主人
[owner_identity] 路人(123456789) -> 其他人
```

## 配置（`config.json`）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `owner_qq` | `[]` | 主人 QQ 列表，字符串数组；空数组 = 谁都不是主人 |
| `owner_extra` | 见示例 | 身份是主人时追加给模型的话 |
| `other_extra` | 见示例 | 身份是其他人时追加给模型的话 |
| `mask_owner_in_name` | `true` | 非主人的昵称里含「主人」时替换成 `＊`，防止型号被名字骗 |
| `mute_file` | `""` | 指向一个文件；文件存在时，群里除 `/` 开头的指令外全部不处理（一键静音）。空 = 关闭 |
| `strip_emoji` | `false` | 回复里去掉 emoji |
| `r1_contract` | `false` | 对推理模型加 `<ans></ans>` 输出约束，并在响应里剥掉思考过程 |
| `r1_provider_marker` | `"R1"` | provider id 里包含这个串才启用上一条 |
| `log_detection` | `true` | 每次请求打一行身份判定日志 |

改完重启 AstrBot 生效（配置在启动时读一次）。

## 配合人格

身份注入只负责「告诉模型谁在说话」，具体亲昵到什么程度由人格提示词决定。配套人格：把「主人撒娇 / 其他人保持距离」写进提示词的双身份人格即可（本仓库只管注入身份，不管语气）。

## 兼容性

- 按 AstrBot 4.x 插件 API 写：`star.Star` + `filter.on_llm_request()` / `on_llm_response()` / `on_decorating_result()` / `event_message_type()`
- 已在 **AstrBot 4.29.3** 上验证：插件加载、身份注入、主人/其他人两条分支（当时是单文件 v1.0.0 形态）
- v2.0.0 把静音、去 emoji、R1 过滤合并进来并参数化，这三个能力来自另一台线上机器（v1.3.0）的实战代码，**合并后未在 4.29.3 上逐项实测**；默认全关，按需打开
- 只改了 `system_prompt` 和 `extra_user_content_parts`，不动人格、不动 provider 配置

## 边界

- 只判断 QQ 号，不认群、不认昵称；换号要改配置
- 插件只影响「调 LLM」这一刻，不改变群里是否 @ 才回复（那是 AstrBot 平台设置）
- 不建议把 `owner_qq` 配成管理员之外的很多人——人一多，「专属感」就没了

## 许可

内部使用。
