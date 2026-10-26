from abc import ABC, abstractmethod

from app.contracts import ComponentProfile


class ComponentProvider(ABC):
    """所有组件参数数据源必须实现的统一接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """返回组件数据 Provider 名称。"""

    @abstractmethod
    def lookup(
        self,
        component_model: str,
    ) -> ComponentProfile | None:
        """根据组件型号查询抗灾和规格参数。"""
