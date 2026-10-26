from abc import ABC, abstractmethod

from app.contracts import WeatherProfile


class WeatherProvider(ABC):
    """所有历史气象数据源必须实现的统一接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """返回气象 Provider 名称。"""

    @abstractmethod
    def lookup(
        self,
        *,
        longitude: float,
        latitude: float,
    ) -> WeatherProfile:
        """查询指定坐标的历史气象统计。"""
