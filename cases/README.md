# Domux Community Case Studies

Real stories from people who downloaded Domux from Hugging Face, ran it on a
smart-home command-understanding task, and published the result in the official
Domux Discussions.

Model: https://huggingface.co/iFlytekOpenSource/Domux

Discussions: https://huggingface.co/iFlytekOpenSource/Domux/discussions

## Publication rule

For this gallery, the public channel is intentionally limited to the official
Domux Discussions. Every accepted case must include at least one URL matching:

    https://huggingface.co/iFlytekOpenSource/Domux/discussions/<number>

Posts on other platforms may be shared separately, but they do not replace the
required Hugging Face Discussion and must not be listed in the channels field.
This keeps download instructions, runtime evidence, questions, and follow-up
answers next to the model that people need to download.

## What a strong case demonstrates

1. A genuine Hugging Face download of iFlytekOpenSource/Domux, not a copied
   output or a ModelScope-only run.
2. A real inference or evaluation run using the downloaded snapshot.
3. The exact Hugging Face revision, runtime, hardware, input, output, and
   enough logs or screenshots for another person to reproduce the result.
4. A substantive Discussion post explaining the task, result, limitations,
   and what the author learned.

## Suggested directions

- Reproduce the official quick start with vLLM or SGLang.
- Test multi-device, multi-room, omitted-slot, correction, or fuzzy commands.
- Evaluate dialect, ASR noise, code-switching, accessibility, or long-tail
  device names.
- Run the open 4,057-sample evaluation and analyze failure clusters.
- Compare latency, memory, throughput, or quantization on available hardware.
- Explore high-risk commands, ambiguity detection, confirmation, and refusal.
- Improve data, evaluation, training, deployment, or integration workflows and
  document a real before/after result.

## Requirements

Every submission must satisfy all of the following:

1. **Hugging Face download.** Download the model from
   iFlytekOpenSource/Domux using hf download, snapshot_download, or Git LFS.
   Record the exact tested revision. Access is gated by the Gemma terms.
2. **Real execution.** Run the downloaded model or a derived artifact produced
   from that snapshot. Include representative inputs and raw structured
   outputs, plus a screenshot or log excerpt.
3. **Hugging Face Discussion.** Publish the full case in the official Domux
   Discussions. A GitHub-only write-up is not sufficient.
4. **Reproducibility.** Record runtime and version, hardware, precision or
   quantization, key parameters, and the command or script used.
5. **Honest metrics.** State sample size and method for every accuracy,
   latency, memory, or throughput result. Do not compare incompatible setups.
6. **Safety and privacy.** Remove tokens, cache paths containing personal
   usernames, private prompts, household data, and internal endpoints.
7. **Original work.** The run and evidence must be the submitter's own. AI may
   help edit prose, but it cannot replace an actual download and experiment.

## How to submit

1. Accept the Gemma access terms on the Domux model page.
2. Download a pinned snapshot from Hugging Face, for example:

       hf download iFlytekOpenSource/Domux --revision <commit-sha>

3. Run a real Domux inference, evaluation, or optimization experiment.
4. Create a new public Discussion with a title beginning:

       [HER Hack-Astron #4] <your case title>

5. Fork this repository and copy cases/TEMPLATE to cases/<case-id>.
6. Fill in every frontmatter field and section. The channels list must contain
   the public Discussion URL.
7. Open a PR titled:

       [case] <case-id> - <one-line result>

Reference the HER Hack-Astron #4 issue with Ref, not Closes, so one case does
not close the whole challenge.

## Directory layout

    cases/
    |-- README.md
    |-- TEMPLATE/
    |   -- README.md
    -- <case-id>/
        |-- README.md
        |-- preview.png
        -- optional supporting files

---

# Domux 社区使用案例

这里收录真实的 Domux 使用故事：参与者从 Hugging Face 下载模型，完成智能家居
指令理解实验，并把完整过程发布在 Domux 官方 Discussions。

## 唯一有效的公开发布渠道

活动案例必须发布到：

https://huggingface.co/iFlytekOpenSource/Domux/discussions

案例 frontmatter 的 channels 只能填写形如下面的链接：

    https://huggingface.co/iFlytekOpenSource/Domux/discussions/<编号>

其他平台可以自愿同步，但不能替代 Hugging Face Discussion，也不能作为活动验收链接。
这样每篇内容都会留在模型页旁边，让读者可以直接阅读、提问、下载并复现。

## 案例必须证明什么

1. 确实从 Hugging Face 下载了 iFlytekOpenSource/Domux；
2. 确实运行了所下载的 snapshot，或基于该 snapshot 生成的量化/微调产物；
3. 记录 Hugging Face revision、运行框架、硬件、输入、原始输出和复现命令；
4. 在官方 Discussion 发布有实质内容的完整案例，并在 GitHub 案例文件中回链。

## 提交步骤

1. 在模型页同意 Gemma 使用条款；
2. 从 Hugging Face 下载固定 revision；
3. 完成真实推理、评测、量化、训练或集成实验；
4. 新建标题以 HER Hack-Astron #4 开头的公开 Discussion；
5. 复制 cases/TEMPLATE 为 cases/<案例 id>；
6. 填写全部字段，channels 附上 Discussion 直达链接；
7. 提交标题为 [case] <案例 id> - <一句话结果> 的 PR，并在描述写 Ref 活动 issue。

详细验收字段见案例模板。不得提交模型权重、token、个人缓存路径、家庭隐私数据或
未经授权的数据。
