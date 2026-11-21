# 素材与第三方许可 · Asset and third-party notices

项目代码按 Apache-2.0 开源。派徕设计标志、产品标识和三款牛马哨兵吉祥物用于识别本项目，复用这些品牌素材请联系派徕设计。[代码许可](LICENSE) · [合作邮箱](mailto:986592286@qq.com)

## 视觉模型

仓库提供上游模型的下载地址和 SHA-256，开发者可用 `packaging/fetch_models.ps1` 获取对应权重。安装包会随应用携带所需模型。[模型清单](models/sources.json)

- Face Landmarker、Hand Landmarker 和 EfficientDet-Lite0 来自 Google AI Edge 的 MediaPipe。[人脸](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) · [手部](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) · [物体](https://ai.google.dev/edge/mediapipe/solutions/vision/object_detector)
- YuNet 来自 OpenCV Zoo，使用 MIT 许可。[YuNet 许可](models/YuNet-LICENSE.txt)
- SFace 来自 OpenCV Zoo，使用 Apache-2.0 许可。[SFace 许可](models/SFace-LICENSE.txt)

## 小猪哨声

其中部分录音取自 Vinrax 的 *Pig SFX Pack*，采用 CC BY 3.0。片段经过裁剪、滤波、音量匹配和淡入淡出处理。其余具体录音、作者、权利状态和来源链接列在音频清单里。[原始音效包](https://opengameart.org/content/pig-sfx-pack) · [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) · [完整音效署名](assets/piglets/credits.json)

## 安装包与品牌

安装版可在「产品介绍」打开第三方组件许可。构建时复制 Python、依赖、模型与安装器的许可文件。第三方材料沿用其各自的许可条款。

## English

The project code is available under Apache-2.0. PLAY atelier's studio mark, product identification marks, and NiuMa Sentinel mascot artwork identify this project. Ask PLAY atelier about reusing those brand assets. [Code license](LICENSE) · [Contact PLAY atelier](mailto:986592286@qq.com)

- **Vision models.** Face Landmarker, Hand Landmarker, and EfficientDet-Lite0 come from Google AI Edge's MediaPipe project. The installer also uses OpenCV Zoo's YuNet (MIT) and SFace (Apache-2.0). The developer download script verifies a pinned SHA-256 for every model. [Sources and hashes](models/sources.json) · [YuNet license](models/YuNet-LICENSE.txt) · [SFace license](models/SFace-LICENSE.txt)
- **Piglet sounds.** Extracts from Vinrax's *Pig SFX Pack* are used under CC BY 3.0. In-app excerpts have been trimmed, filtered, leveled, and faded. Other recordings have individual credits and terms in the same manifest. [Sound credits](assets/piglets/credits.json) · [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)
- **Installer components.** In the installed app, open the third-party notices from About. The Windows package includes the notices for Python, dependencies, models, and the installer.
