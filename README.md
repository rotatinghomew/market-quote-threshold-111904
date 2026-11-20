# 早市整车优惠

资料来自合成的市场询报价、班车及退货记录。不连接交易系统。

`data` 保存原始记录，`DATA.md` 是资料字典。`src/market_analysis` 是只读分析
应用（资格还原、门槛诊断、断点估计、净贡献核算、候选门槛试算、报价审计与余量
滚动），`reports/report.md` 是由数据现算生成的分析报表，`ANALYSIS.md` 说明
方法、证据边界与每个候选门槛的结论依据。试算不产生真报价，也不写回 `data`。

环境使用 Python 3.11.2。安装锁定的依赖并检查资料与分析：

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

生成报表与按报价编号回查（无需修改数据）：

```sh
PYTHONPATH=src python3 -m market_analysis.report --data data --out reports/report.md
PYTHONPATH=src python3 -m market_analysis.audit_cli --data data --quote Q4403
```

检查只读取本地数据，不访问业务系统。
