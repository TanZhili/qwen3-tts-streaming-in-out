# 流式文本输入 + 流式音频输出演示

这个目录演示一件事：大模型的文字还在往外冒的时候，就把已经生成的片段送进 Qwen3-TTS，并在第一包音频就绪时立刻拿到它。后到的文字留在同一次合成会话里，后面的音频块可以看见更长的上下文，而不是只被第一小段冻住。

本机没有 NVIDIA GPU，也没有缓存的 Qwen3-TTS 权重，所以这里**没有跑模型推理**，日志里的 `ttfa_ms` 是这次进程里真实测到的管线时间，不是论文或各仓库公布的首包延迟。占位 PCM 只用来证明“音频包已经生成并写成了 WAV”。

## 为什么选这个仓库

选定的实现是 [X-Square-Robot/Qwen3TTS-Streaming](https://github.com/X-Square-Robot/Qwen3TTS-Streaming)（MIT）。

它是目前公开资料里，同时满足下面两条、并且公布的首包延迟最低的一套：

1. **文本可以在合成已经开始之后继续追加。** 调度器对“解码进行到一半、上游 LLM 暂时没有新 token”有专门状态 `WAIT_TEXT`：KV 留着，解码暂停，新文本到达后在同一次会话里接着解码。第一包音频不必等整段 LLM 回复结束。
2. **音频按包往外送。** 原生 WebSocket `/v1/ws` 在 `audio_header` 之后发二进制 PCM。

作者在 README 的 Performance Claims 里给出的单请求数字（RTX 5090，`custom-1.7b`，bf16，前缀缓存命中）：

| 测量 | 数字 | 条件 |
| --- | --- | --- |
| 2026-09-10 引擎刷新，SDK 客户端 TTFT | WebSocket **23.290 ms**，gRPC **24.593 ms**（平均） | 单请求，固定文本/说话人，缓存命中 |
| 2026-07-08 历史基线 | 服务端 **14.9 ± 0.3 ms**（n=50，最小 14.4，p99 15.9）；复用本地 gRPC 通道时客户端大约 16.1 ms p50 | 同一类 RTX 5090 / all-bf16 / 缓存命中 |

同一篇 README 写明：2026-09-10 的 128 路并发延迟比历史基线差一截，**不能**当成吞吐能力的承诺。上面两行都是作者自称的测量，本仓库没有在这台机器上复现。

官方技术报告里的 97 ms（0.6B）/ 101 ms（1.7B）是模型架构的端到端首包数字，硬件细节没有和 X-Square 这组 RTX 5090 数字对齐。Callum Macpherson 在 A10G 上用另一套后端测量时，四帧首包大约 194 ms p50，并写明没有复现 97 ms。这几组数字测的不是同一个栈，不能直接比大小。

## 对比

“流式文本输入”这一列只在**合成已经开始之后，新到的文本还能进入同一次解码**时记为支持。全文先到齐、再把音频切块送出，记为不支持。括号里是公开材料里的原话或转述，互相冲突的地方单独写出。

| 项目 | 流式文本输入 | 流式音频输出 | 公开的首包 / TTFA | 来源 |
| --- | --- | --- | --- | --- |
| [X-Square-Robot/Qwen3TTS-Streaming](https://github.com/X-Square-Robot/Qwen3TTS-Streaming) | 支持。token 级输入，文本断档时 `WAIT_TEXT`，KV 保留，同一次会话继续 | 支持。二进制 PCM | 单请求：2026-09-10 SDK 客户端 WebSocket 23.290 ms / gRPC 24.593 ms；历史服务端 14.9 ± 0.3 ms。128 路并发另一套数字，且新测量变差 | [README Performance Claims](https://github.com/X-Square-Robot/Qwen3TTS-Streaming) |
| [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 官方 `qwen-tts` | 架构是双轨流式；发布的 Python 包只模拟 prompt 排列，维护者说明这不是真流式输入 | 包本身不做音频流式，指向 vLLM-Omni | 论文 / README：0.6B **97 ms**，1.7B **101 ms**。这是模型宣称的端到端首包，不是这个包测出来的墙钟 | [论文](https://arxiv.org/html/2601.15621)，[Discussion #64](https://github.com/QwenLM/Qwen3-TTS/discussions/64)，[Issue #10](https://github.com/QwenLM/Qwen3-TTS/issues/10) |
| [andimarafioti/faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts) | 不支持现场追加。`non_streaming_mode=False` 是把**已经完整的文本**逐步喂进解码 | 支持。按 `chunk_size` 吐出音频块 | Jetson AGX Orin 0.6B：`chunk_size=1` 时 **240 ms**，`2` 时 266 ms。RTX 4090、1.7B、`chunk_size=8` 大约 **149–159 ms**（README 不同小节分别写了 152±11、149±1、148±1，以及 ICL 159 ms）。Callum 在 A10G、一帧一包测到 **107.8 ms p50** | [README](https://github.com/andimarafioti/faster-qwen3-tts)，[博客](https://github.com/andimarafioti/faster-qwen3-tts/blob/main/BLOG.md)，[Callum 的测量](https://callumjmac.github.io/posts/qwen-tts-streaming/) |
| [dffdeeq/Qwen3-TTS-streaming](https://github.com/dffdeeq/Qwen3-TTS-streaming) 及 [rekuenkdr](https://github.com/rekuenkdr/Qwen3-TTS-streaming)、[xxddccaa](https://github.com/xxddccaa/Qwen3-TTS-streaming) 等 fork | 不支持。调用时传入整段文本 | 支持。`stream_generate_*` 按帧吐 PCM | README 把默认 `emit_every_frames=4` 写成 12 Hz 下约 **0.33 s 音频**。这是块长，不是单独公布的墙钟 TTFA。DeepWiki 把首包说成约 0.33 s，和块长用了同一个数 | [README](https://github.com/dffdeeq/Qwen3-TTS-streaming) |
| [qwen3-tts-streaming](https://pypi.org/project/qwen3-tts-streaming/)（PyPI，`fast_tts`） | 不支持。先 `split_segments()`，再对每段做流式生成 | 支持 | RTX 5060 Ti 上作者称优化后 TTFA **约 355 ms**（优化前约 40 s） | [PyPI](https://pypi.org/project/qwen3-tts-streaming/) |
| [vLLM-Omni](https://github.com/vllm-project/vllm-omni) | WebSocket 可以逐段收文本，但合成开始于句边界或 `input.done`。PR #4731 改成等 `input.done` 后做**一次**请求。进行中的解码不会再吃进新 token | 支持。`stream=true` 的 PCM / SSE | 文档强调 async chunk 用来降低音频侧首包延迟，**没有**给出和上面同一口径的 token 级 TTFA | [Speech API](https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/speech_api/)，[PR #4731](https://github.com/vllm-project/vllm-omni/pull/4731)，[PR #1230](https://github.com/vllm-project/vllm-omni/pull/1230) |
| [SGLang-Omni](https://github.com/sgl-project/sglang-omni) | 请求带着完整文本。流式的是 codec / 波形，不是边生成边追加的文本 | 支持 | 一张 H100、CustomVoice 1.7B、1 rps：first playable p50 **28.4–28.7 ms**；20 rps 时大约 36.9–37.2 ms，可听首包大约 38 ms。这是全文请求的音频流式 | [Issue #1754](https://github.com/sgl-project/sglang-omni/issues/1754) |
| [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio) | 不支持现场追加。`stream=True` 只切音频块 | 支持 | 文档默认 `streaming_interval=2.0` 秒；Qwen3-TTS 示例用 0.32 秒（约 4 个 12.5 Hz token）。没有单独的 TTFA 表 | [Streaming 指南](https://blaizzy.github.io/mlx-audio/guides/streaming/) |
| [NVIDIA TensorRT-Edge-LLM](https://github.com/NVIDIA/TensorRT-Edge-LLM) | 文档写明文本按整段请求消费，**流式文本输入尚未支持** | 支持。`--streaming --chunk-frames` | 首个可播放音频随 `chunk-frames` 变长：要先攒满一块再做 Code2Wav，约 `N/12.5` 秒音频内容再加声码器时间。25 帧大约是 2 秒音频内容量级 | [TTS 文档](https://github.com/NVIDIA/TensorRT-Edge-LLM/blob/main/docs/source/user_guide/examples/tts.md) |
| [malaiwah/qwen3-tts-server](https://github.com/malaiwah/qwen3-tts-server) | 不支持。底层是 faster-qwen3-tts，一次请求一段文本 | 支持。`/v1/audio/speech/pcm-stream` | RTX 4080 SUPER 上第一包 PCM **约 130–131 ms**（320 ms 音频 / 7680 samples） | [README](https://github.com/malaiwah/qwen3-tts-server/blob/main/README.md) |
| TensorRT-LLM | 没有 Qwen3-TTS 实现。对应 issue 已关闭，近期不计划做 | — | — | [Issue #11118](https://github.com/NVIDIA/TensorRT-LLM/issues/11118) |

MLX 只适合 Apple Silicon。TensorRT 引擎和具体 GPU / 驱动绑在一起，X-Square 的快速路径要在 Linux 上用 NVIDIA 容器编译。

## 演示里实际跑的是什么

有三种后端，测量口径相同：

| `--backend` | 行为 |
| --- | --- |
| `local`（默认） | 进程内会话。第一个文本单元一到就生成第一包 PCM；之后按 `--decode-step-ms` 继续出包，每一包读取**当时缓冲区里的全部文本**。后到的字留在同一次会话里。波形是 440 Hz 一带的占位正弦，不是语音。 |
| `loopback` | 用真实 WebSocket 跑 X-Square 原生帧：`start` / `text` / `stop`，然后 `audio_header` + 二进制 PCM。对端是本进程里的小服务器，收到第一帧文本就回第一包，不等 `stop`。用来核对客户端的发送点和收包点。同样不是模型。 |
| `xsquare` | 连到已经部署好的引擎，默认 `ws://127.0.0.1:50052/v1/ws`。`ttfa_ms` 是客户端观测值：第一帧 `text` 写出之前的 `perf_counter`，到第一帧二进制 PCM 到达。 |
| `auto` | 先试 `--url`，连不上就退回 `local`，并打印 `xsquare_unavailable`。 |

接入真实 LLM 的位置是 `demo/feed.py` 里的 `iter_llm_deltas()`。它是一个异步生成器，产出文本增量。默认实现只把演示字符串切成单元，不睡眠。`feed_tts()` 负责送进 TTS：

- 第一个单元立刻送出。这一下就是 `first_text_in`。
- `--interval-ms` 默认 **240**。从第一次送入的 `perf_counter` 起，第 i 个单元（从 0 数）的送入时刻目标是 `t0 + i * 240ms`。
- `--interval-ms 0` 时，生成器每产出一段就立刻送入，不再排网格。接真实模型流时用这个，避免再人为拖到 240 ms。

### 词的切分

1. 文本里只要有空白，就按空白切开。一个空白 token 是一个词。除最后一个词以外，送入 TTS 的单元带一个尾随空格，缓冲区拼回去仍是正常句子。`Hello world` → `Hello `、`world`。
2. 没有空白时，按标点切成短句，标点留在前一个单元上。默认中文演示走这条规则。
3. 没有空白也没有标点时，每两个字一个单元，最后剩一个字就单独成单元。`你好世界` → `你好`、`世界`。

默认句子：

`你好，这是流式文本输入。第一包不等全文，后面的词会继续到达。后续音频可以看到更长的上下文。`

切成五段：`你好，` / `这是流式文本输入。` / `第一包不等全文，` / `后面的词会继续到达。` / `后续音频可以看到更长的上下文。`

## 时间戳

全部用同一条 `time.perf_counter` 时间轴。`*_ms` 是距进程内时钟原点的毫秒。`ttfa_ms` 是两个原始计数器相减，不是拿日志里的小数估出来的。`wall=` 是本地时区的 ISO-8601 时间，只给人看，不参与减法。

| 日志字段 | 含义 |
| --- | --- |
| `llm_first_text_ms` | 模拟 LLM 吐出第一个单元的时刻（调用 `send_text` 之前） |
| `first_text_in_ms` | 这个单元交给 TTS 的时刻。本地后端是写入会话缓冲的时刻；WebSocket 后端是 `text` 帧发送调用之前 |
| `first_audio_out_ms` | 第一包 PCM 已经在内存里的时刻。本地后端是正弦样本生成完；WebSocket 后端是第一帧二进制音频读到的时刻 |
| `ttfa_ms` | `first_audio_out` 的计数器减去 `first_text_in` 的计数器，单位毫秒 |
| `word_send gap_ms` | 这一次送入和上一次送入的间隔。第 0 个是 0。其余应落在 240 ms 附近 |
| `later_context extra_text` | 这一包可见文本里，超出第一包当时文本的部分 |
| `audio_chunk visible_text` | 生成或收到这一包时，会话已经看见的全部文本 |

本地后端在第一包之后才按 `--decode-step-ms`（默认 200）继续出包。这个间隔**不**加在第一包前面。它只是让后续包落在“又有新文本到达”之后，用来观察上下文变长。

## 怎么运行

需要 Python 3.12（本机是 3.12.10）。`local` 只用标准库。`loopback` 和 `xsquare` 需要：

```powershell
pip install -r requirements.txt
```

在仓库根目录：

```powershell
cd D:\projects\qwen3-tts-streaming-in-out
python -m demo
python -m demo --interval-ms 240 --backend local
python -m demo --backend loopback
python -m demo --text "Hello streaming audio starts now"
```

成功时最后一行是 `summary ... check=pass`。音频在 `output/`：

- `chunk_000.wav`、`chunk_001.wav`…… 每一包
- `growing.wav` 按包增长后的整段
- `timings.json` 同一组数字

采样率 24000 Hz，单声道 16 bit。一包对应模型的一帧 12.5 Hz，也就是 1920 个样本、80 ms 音频。这是占位包的长度，不是测到的模型解码时间。

连到已经启动的引擎：

```powershell
python -m demo --backend xsquare --url ws://127.0.0.1:50052/v1/ws --speaker Serena --language Chinese
```

协议和 [引擎 README 的 Streaming Protocol](https://github.com/X-Square-Robot/Qwen3TTS-Streaming) 一致：`start`、多次 `text`、`stop`，接收 `audio_header` 和二进制 PCM。说话人、`task_type=custom_voice` 与 `custom-1.7b` 检查点对应。

## 硬件和权重

要听到真正的 Qwen3-TTS，需要引擎作者写明的环境，而不是这台 Windows 机器：

- Linux x86-64
- NVIDIA GPU，建议不少于 16 GB 显存（1.7B + KV + TensorRT）
- Docker、NVIDIA Container Toolkit、对应驱动
- 磁盘大约 20–40 GB（权重、ONNX、TensorRT 引擎）
- 模型是 Qwen 的 CustomVoice 1.7B（引擎变体名 `custom-1.7b`）。权重许可证以 Hugging Face / ModelScope 模型卡为准。本仓库代码路径是 MIT，上游 Qwen3-TTS 是 Apache-2.0，TensorRT / Triton 镜像另受 NVIDIA 条款约束

引擎会在第一次 `setup` 时自己下载权重。本演示没有下载多 GB 权重。在一台符合要求的 Linux GPU 机器上：

```bash
git clone --recursive https://github.com/X-Square-Robot/Qwen3TTS-Streaming.git
cd Qwen3TTS-Streaming
bash scripts/bash/autorun.sh all -m custom-1.7b
```

默认 WebSocket 是 `ws://localhost:50052/v1/ws`。起来之后回到本目录，用上一节的 `--backend xsquare` 把同一套计时打到真引擎上。

这台开发机上 `nvidia-smi` 不存在，Hugging Face 缓存里也没有 Qwen3-TTS 权重，因此没有执行模型前向。
