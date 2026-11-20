"""早市整车优惠门槛分析包。

模块分工：
- data：只读加载合成资料；
- eligibility：按询价时意向量与豁免还原原始资格、按实装还原兑付；
- diagnostics：扎堆、同点混淆、跨边买方三类有效性诊断；
- causal：门槛邻域的局部线性断点估计（带聚类标准误与适用范围）；
- accounting：按观察时点的净贡献、成熟度与运费可核对覆盖；
- simulation：候选门槛的证据分级、资源预留与批准/试用/拒绝试算；
- audit：按报价编号还原当时门槛、资格、预算与净贡献。
"""

from .data import load_dataset

__all__ = ["load_dataset"]
