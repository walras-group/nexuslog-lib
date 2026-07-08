# NexusLog

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)
![Version](https://img.shields.io/pypi/v/nexuslog?color=blue)

高性能异步日志库，兼容 Python 标准 logging API。

[English](README.md)

## 性能测试

<p align="center">
  <img src="assets/bench.png" width="720" alt="Benchmark chart" />
</p>

```
Benchmarking with 1,000,000 log messages

------------------------------------------------------------
Logger               Time (s)     Msgs/sec        Log size    
------------------------------------------------------------
loguru               7.675        130,297         89,888,890 bytes
Python logging       5.313        188,206         82,888,890 bytes
picologging          2.038        490,626         79,888,888 bytes
spdlog               0.199        5,034,527       79,888,890 bytes
NexusLogger          0.049        20,304,036      97,888,890 bytes
NexusLogger unix_ts  0.049        20,451,884      83,868,922 bytes
------------------------------------------------------------

NexusLogger is 4.03x faster than spdlog
NexusLogger is 41.38x faster than picologging
NexusLogger is 107.88x faster than Python logging
NexusLogger is 155.83x faster than loguru
NexusLogger unix_ts is 4.06x faster than spdlog
NexusLogger unix_ts is 41.69x faster than picologging
NexusLogger unix_ts is 108.67x faster than Python logging
NexusLogger unix_ts is 156.96x faster than loguru
```

## 安装

```bash
pip install nexuslog
```

## 快速开始

```python
import nexuslog as logging

logging.basicConfig(level=logging.INFO)

logging.info("Hello, world!")
logging.warning("This is a warning")
logging.error("This is an error")
```

## API

### 日志级别

```python
logging.TRACE
logging.DEBUG
logging.INFO
logging.WARNING
logging.ERROR
```

### 模块级函数

```python
logging.basicConfig(filename=None, level=logging.INFO, unix_ts=False)
logging.basicConfig(
    level=logging.INFO,
    name_levels={"db": logging.DEBUG, "http.client": logging.WARNING},
)
logging.trace(message)
logging.debug(message)
logging.info(message)
logging.warning(message)
logging.error(message)
```

### Logger 类

```python
from nexuslog import Logger, Level

logger = Logger("myapp", path="/var/log/app", level=Level.Info)
logger.info("message")
logger.shutdown()
```

### getLogger

```python
import nexuslog as logging

logging.basicConfig(filename="/var/log/app.log", level=logging.DEBUG)
logger = logging.getLogger("myapp")
logger.info("message")
```

## License

MIT
