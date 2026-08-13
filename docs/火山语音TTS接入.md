# 火山语音 TTS 接入实战(实测记录)

本文档记录 book2audio 接入火山引擎语音服务的完整过程与踩坑结论,供后续开发
(TTS2.0 集成进 CLI、seed-audio 增强、LLM 情感标注等)直接参考。所有结论均来自
2026-08 实测。

## 1. 两套独立服务体系(最容易搞混的点)

火山语音有**两套互相独立的服务**,key 不通用、端点不同、开通位置不同:

| | 方舟(Ark) | 语音技术(openspeech) |
|---|---|---|
| 控制台 | console.volcengine.com/ark | console.volcengine.com 搜索"语音技术" |
| API Key | `ARK_API_KEY`(UUID,Bearer 鉴权) | `OPENSPEECH_API_KEY`(UUID,`X-Api-Key` 头) |
| 鉴权头 | `Authorization: Bearer <key>` | `X-Api-Key: <key>` |
| Base URL | `https://ark.cn-beijing.volces.com/api/v3` | `https://openspeech.bytedance.com/api/v3/tts` |
| LLM | `/responses`, `/chat/completions` | 无 |
| TTS | `/tts`(doubao-tts-1-xxx) | `/unidirectional` 等(seed-tts-2.0) |
| 音频生成 | 无 | `/create`(seed-audio-1.0) |

**关键**:方舟 key 拿去调 openspeech 会 401 `Invalid X-Api-Key`;openspeech key 也
不能用于方舟 Bearer 鉴权。book2audio.ini 里两者都要配(LLM 标注用方舟,TTS 用
openspeech)。

## 2. 开通检查(账号侧)

- 方舟:可查 `GET /api/v3/models`(Bearer),看账号开通了哪些模型。**注意**:
  `/models` 列表里通常不包含语音 TTS 模型(TTS 属于语音技术体系),方舟侧只有
  LLM/多模态。
- openspeech:无公开列表 API(需要控制台看)。直接调 `POST /unidirectional`
  探活,看错误码判断:
  - `401 45000010 Invalid X-Api-Key` → key 不对/未开通
  - `403 45000030 requested resource not granted` → 该资源(id)未开通
  - `200 55000000 resource ID is mismatched with speaker related resource`
    → 资源已开通,但**音色不属于该资源**(通常用了 1.0 的 mars 音色调 2.0)
  - `200` 且 data 有 base64 → 成功

## 3. TTS2.0(seed-tts-2.0)—— 推荐主引擎

### 3.1 请求格式(HTTP 单向流式)

```
POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
X-Api-Key: <openspeech key>
X-Api-Resource-Id: seed-tts-2.0
Content-Type: application/json
Connection: keep-alive
```

```json
{
  "req_params": {
    "text": "要合成的文本",
    "speaker": "zh_female_wenroushunv_uranus_bigtts",
    "audio_params": { "format": "mp3", "sample_rate": 24000 }
  }
}
```

⚠️ **参数名是 `req_params.speaker`,不是 `voice_type`/`voice`**。用错参数名或
平铺 body 会一直 55000000。这是最初"代码调用不对"的根因。

### 3.2 响应(流式,逐行 JSON)

每行一个 JSON,`data` 字段是 base64 音频片段,需拼起来:

```json
{"code":0,"message":"","data":"<base64 mp3 chunk>"}   // 音频块(多条)
{"code":20000000,"message":"OK","data":null}          // 结束标记
```

- 大部分行是明文 JSON;个别行可能 gzip 压缩(`\x1f\x8b` 开头),需先解压再 parse。
- 用 `requests` 的 `stream=True` + `iter_lines()` 逐行读,边读边拼。
- 实测:900 字 → 约 240 秒音频,耗时约 27 秒;1500 字 → 479 秒音频,约 52 秒。
  建议分块 900 字(速度/粒度平衡)。

### 3.3 音色(uranus 2.0 系列,全部可用)

实测可用(seed-tts-2.0):

```
zh_female_vv_uranus_bigtts              Vivi 2.0 通用
zh_female_wenroushunv_uranus_bigtts     温柔淑女 2.0 通用(本项目选定)
zh_female_linjianvhai_uranus_bigtts     邻家女孩 2.0
zh_female_tianmeixiaoyuan_uranus_bigtts 甜美小源 2.0
zh_female_gaolengyujie_uranus_bigtts    高冷御姐 2.0
zh_female_xiaoxue_uranus_bigtts         儿童绘本 2.0 有声阅读
zh_male_fanjuanqingnian_uranus_bigtts   反卷青年 2.0(男)
zh_female_shuangkuaisisi_uranus_bigtts  爽快思思 2.0
zh_male_wenrouxiaoge_uranus_bigtts      温柔小哥 2.0(男)
```

⚠️ **1.0 的 mars 音色(`*_mars_bigtts`)不可用于 seed-tts-2.0**(55000000);
2.0 用 `*_uranus_bigtts`。多情感音色(emo 系列)属 1.0,同样不支持。

## 4. seed-audio-1.0(生成式音频,备用)

```
POST https://openspeech.bytedance.com/api/v3/tts/create
X-Api-Key: <openspeech key>
```

```json
{
  "model": "seed-audio-1.0",
  "text_prompt": "文本(可含音效/角色/情绪描述)",
  "references": [{"speaker": "zh_female_vv_uranus_bigtts"}],
  "audio_config": {"format": "mp3", "sample_rate": 48000, "pitch_rate": 0, "speech_rate": 0, "loudness_rate": 0},
  "watermark": {}
}
```

- 响应 `{"audio": "<base64 mp3>"}`,**一次直出**,无需轮询。
- ⚠️ **必须传 `references.speaker` 固定音色**,否则每次生成随机音色
  (实测出现"一段女声一段男声"交替)。用户给的官方样例省略了该字段,是坑。
- 单次约 ≤120 秒音频(约 550 字),速度慢(90 字约 19 秒音频,生成 60-90 秒),
  全书 29 万字约 12-16 小时。TTS2.0 约 1.5-2 小时,快 8-10 倍。
- 仅接受 uranus 2.0 音色(`*_uranus_bigtts`);mars 音色报 400 speaker not found。

## 5. LLM 情感标注(方舟侧,可选)

- 用方舟 `ARK_API_KEY` + 对话模型(实测 `doubao-seed-evolving` 可用)。
- 端点走 **`/api/v3/responses`**(Responses API),`chat/completions` 对该模型
  会 404 ModelNotOpen。请求:

```
POST https://ark.cn-beijing.volces.com/api/v3/responses
Authorization: Bearer <ark key>
{"model": "doubao-seed-evolving", "input": [{"role":"user","content":[...]}]}
```

- 用途:让 LLM 把章节文本改写成带 SSML 情感标注(`<mstts:express-as>`/`<break>`/
  `<prosody>`)的朗读稿。CLI 的 `chunk --llm-model` 当前走 chat/completions,
  需改为 responses 端点才能真正用上(见"待办")。

## 6. 扫描版 PDF OCR 流程

1. `scripts/ocr_pdf.py <pdf> <out_dir>`:rapidocr_onnxruntime 逐页识别,
   200dpi,约 5 秒/页,输出 `p0000.txt`...,断点续跑(已存在的页跳过)。
   - 首次运行自动下载模型;网络受限先设 `HTTP_PROXY`/`HTTPS_PROXY=127.0.0.1:1088`。
2. `scripts/build_book.py <ocr_dir> <work_dir> <title> <author> --pdf <pdf>`:
   自动扫描"第X章/附录/注释/参考文献"标题定位章节边界(跳过目录页),清洗页眉
   (`<页码>不平等的童年`)、孤立页码,提取封面,输出 `book.json`。
- 章节识别失败可 `--chapters` 手动指定 `起始页:标题,...`。
- 标题合并的已知限制:若 OCR 把副标题拆成多行,只取到首行(如"第七章 语言作为社交生活的渠道："),
  不影响正文与边界;目录页自动跳过。
3. `scripts/synth_tts2.py <work_dir>/book.json`(见下)。
4. `book2audio package <work_dir>/book.json --config book2audio.ini --out output`。

OCR 质量:rapidocr 中文识别良好,但音译人名/个别字有错(如"协作"→"办作"、
"自己"→"自已")、中英文粘连(如 `GarrettTallinger`)。有声书场景可接受;
若要精校需人工或 LLM 校对。

## 7. 目录约定

- `book2audio package` 期望:工作目录 `<out>/.<书名>.work/`,内含 `book.json`
  和 `staging/*.mp3`(全局顺序编号 `0001.mp3...`,与 package 的块顺序对应)。
- 本项目统一:work 目录 `.output.work/`,staging 在其下。
- `synth_tts2.py` 分块时把 `chunks` 写回 book.json(package 依赖 chunks 计算块数)。

## 8. 待办(后续功能方向)

- [ ] 把 openspeech TTS2.0 集成进 CLI 的 `synth` provider(新增 provider 名,
      如 `openspeech_tts`),替代脚本方式,支持 `--chapters`/断点/`--speed`。
- [ ] LLM 情感标注从 chat/completions 改为 Responses API(`/api/v3/responses`)。
- [ ] seed-audio 的 `references.speaker` 参数补进 CLI 的 SeedAudioProvider。
- [ ] 多音色配置化(ini 支持音色映射,按章节/情绪切换)。
- [ ] `--speed` 语速参数透传 TTS2.0(`speed_ratio`)。
- [ ] 封面/章节标题的 LLM 校对(可选,提升 OCR 文本质量)。

## 9. 关键错误码速查

| 码 | 含义 | 处理 |
|---|---|---|
| 401 45000010 | X-Api-Key 无效/未开通 | 检查 openspeech key |
| 403 45000030 | 资源未开通 | 控制台开通对应资源 |
| 400 45001115 | speaker 不在音色库 | 换 uranus 2.0 音色 |
| 200 55000000 | 音色与资源不匹配 | 用 `*_uranus_bigtts` + `seed-tts-2.0` |
| 45000292 | 并发配额超限 | 稍后重试(concurr 资源) |
