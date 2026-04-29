import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SecurityFilter:
    DANGEROUS_KEYWORDS = [
        "注销", "退出登录", "sign out", "log out", "logout",
        "删除", "delete", "remove",
        "清除", "clear", "erase",
        "卸载", "uninstall",
        "支付", "payment", "pay", "转账", "transfer",
        "绑定银行卡", "bank card", "credit card",
        "修改密码", "change password", "reset password",
        "开通会员", "subscribe", "vip",
        "购买", "buy", "purchase",
    ]
    
    SAFE_KEYWORDS = [
        "登录", "登录", "sign in", "log in", "login",
        "注册", "register", "sign up",
        "返回", "back", "返回",
        "取消", "cancel",
        "下一步", "next", "上一步", "previous",
        "提交", "submit",
        "保存", "save",
        "更多", "more", "菜单", "menu",
    ]
    
    def __init__(
        self,
        target_package: str,
        dangerous_keywords: Optional[list[str]] = None,
    ):
        self.target_package = target_package
        self.dangerous_keywords = dangerous_keywords or self.DANGEROUS_KEYWORDS
        self._blocked_count = 0
    
    def is_dangerous(self, element_text: str, element_id: str = "") -> bool:
        combined = f"{element_text} {element_id}".lower()
        
        for keyword in self.dangerous_keywords:
            if keyword.lower() in combined:
                logger.warning(f"🛑 检测到危险关键词: {keyword}")
                self._blocked_count += 1
                return True
        
        return False
    
    def is_safe_action(self, element_text: str) -> bool:
        combined = element_text.lower()
        
        for keyword in self.SAFE_KEYWORDS:
            if keyword.lower() in combined:
                return True
        
        return False
    
    def should_explore(
        self,
        element_text: str,
        element_id: str,
        current_package: str,
    ) -> tuple[bool, str]:
        if current_package != self.target_package:
            logger.warning(f"🛑 包名边界检查失败: 当前 {current_package} != 目标 {self.target_package}")
            return False, "package_mismatch"
        
        if self.is_dangerous(element_text, element_id):
            return False, "dangerous_action"
        
        return True, "allowed"
    
    def get_blocked_count(self) -> int:
        return self._blocked_count
    
    def reset_counter(self) -> None:
        self._blocked_count = 0


class PackageNameGuard:
    SYSTEM_PACKAGES = [
        "com.android.",
        "com.google.android.gms",
        "com.google.android.apps",
        "com.android.browser",
        "com.android.settings",
        "com.android.vending",  # Play Store
    ]
    
    BROWSER_PACKAGES = [
        "com.android.browser",
        "com.android.chrome",
        "com.google.android.browser",
        "com.opera.browser",
        "com.microsoft.emes",
        "com.baidu.searchbox",
    ]
    
    def __init__(self, target_package: str):
        self.target_package = target_package
    
    def is_valid_package(self, package_name: str) -> bool:
        if not package_name:
            return False
        
        if package_name == self.target_package:
            return True
        
        for sys_pkg in self.SYSTEM_PACKAGES:
            if package_name.startswith(sys_pkg):
                return False
        
        return True
    
    def is_browser_redirect(self, package_name: str) -> bool:
        for browser_pkg in self.BROWSER_PACKAGES:
            if browser_pkg in package_name:
                return True
        return False
    
    def check_transition(
        self,
        from_package: str,
        to_package: str,
    ) -> tuple[bool, str]:
        if to_package == self.target_package:
            return True, "back_to_app"
        
        if to_package == from_package:
            return True, "same_package"
        
        if self.is_browser_redirect(to_package):
            logger.warning(f"🛑 检测到浏览器跳转: {to_package}")
            return False, "browser_redirect"
        
        for sys_pkg in self.SYSTEM_PACKAGES:
            if to_package.startswith(sys_pkg):
                logger.warning(f"🛑 检测到系统包跳转: {to_package}")
                return False, "system_package"
        
        if not to_package.startswith(self.target_package.split(".")[0]):
            logger.warning(f"🛑 检测到跨应用跳转: {to_package}")
            return False, "cross_app"
        
        return True, "allowed"


def create_security_filter(target_package: str) -> SecurityFilter:
    return SecurityFilter(target_package=target_package)


def create_package_guard(target_package: str) -> PackageNameGuard:
    return PackageNameGuard(target_package=target_package)
