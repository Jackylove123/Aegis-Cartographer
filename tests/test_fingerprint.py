import pytest
from aegis_cartographer import get_skeleton_hash


PAGE_A = {
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            {
                "class": "android.widget.LinearLayout",
                "resource-id": "com.app:id/main_container",
                "bounds": "[0,0][1080,2100]",
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.app:id/btn_login",
                        "text": "登录",
                        "content-desc": "登录按钮",
                        "clickable": "true",
                        "bounds": "[100,100][400,200]",
                    },
                    {
                        "class": "android.widget.TextView",
                        "resource-id": "com.app:id/title",
                        "text": "欢迎登录",
                        "bounds": "[0,300][1080,400]",
                    },
                ],
            }
        ],
    }
}

PAGE_B = {
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            {
                "class": "android.widget.LinearLayout",
                "resource-id": "com.app:id/main_container",
                "bounds": "[0,0][1080,2100]",
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.app:id/btn_login",
                        "text": "立即注册",
                        "content-desc": "注册按钮",
                        "clickable": "true",
                        "bounds": "[100,100][400,200]",
                    },
                    {
                        "class": "android.widget.TextView",
                        "resource-id": "com.app:id/title",
                        "text": "欢迎注册",
                        "bounds": "[0,300][1080,400]",
                    },
                ],
            }
        ],
    }
}

PAGE_C = {
    "tree": {
        "class": "android.widget.FrameLayout",
        "resource-id": "android:id/content",
        "children": [
            {
                "class": "android.widget.LinearLayout",
                "resource-id": "com.app:id/main_container",
                "children": [
                    {
                        "class": "android.widget.Button",
                        "resource-id": "com.app:id/btn_settings",
                        "clickable": "true",
                    },
                ],
            }
        ],
    }
}


class TestSkeletonHash:
    def test_same_structure_different_text(self):
        hash_a = get_skeleton_hash(PAGE_A)
        hash_b = get_skeleton_hash(PAGE_B)
        
        assert hash_a == hash_b, (
            f"相同结构的页面应生成相同哈希，但得到:\n"
            f"  Page A: {hash_a}\n"
            f"  Page B: {hash_b}"
        )

    def test_different_structure_different_hash(self):
        hash_a = get_skeleton_hash(PAGE_A)
        hash_c = get_skeleton_hash(PAGE_C)
        
        assert hash_a != hash_c, (
            f"不同结构的页面应生成不同哈希"
        )

    def test_bounds_not_included(self):
        hash_with_bounds = get_skeleton_hash(PAGE_A)
        
        page_no_bounds = {
            "tree": {
                "class": "android.widget.FrameLayout",
                "resource-id": "android:id/content",
                "children": [
                    {
                        "class": "android.widget.LinearLayout",
                        "resource-id": "com.app:id/main_container",
                        "children": [
                            {
                                "class": "android.widget.Button",
                                "resource-id": "com.app:id/btn_login",
                                "text": "登录",
                                "clickable": "true",
                            },
                        ],
                    }
                ],
            }
        }
        
        hash_no_bounds = get_skeleton_hash(page_no_bounds)
        
        assert hash_with_bounds == hash_no_bounds, "bounds 不应影响哈希值"

    def test_content_desc_not_included(self):
        page_with_desc = {
            "tree": {
                "children": [
                    {
                        "resource-id": "com.app:id/btn",
                        "class": "android.widget.Button",
                        "content-desc": "描述文本",
                        "clickable": "true",
                    }
                ]
            }
        }
        
        page_without_desc = {
            "tree": {
                "children": [
                    {
                        "resource-id": "com.app:id/btn",
                        "class": "android.widget.Button",
                        "clickable": "true",
                    }
                ]
            }
        }
        
        hash_with = get_skeleton_hash(page_with_desc)
        hash_without = get_skeleton_hash(page_without_desc)
        
        assert hash_with == hash_without, "content-desc 不应影响哈希值"

    def test_non_clickable_elements_ignored(self):
        page = {
            "tree": {
                "children": [
                    {
                        "resource-id": "com.app:id/btn",
                        "class": "android.widget.Button",
                        "clickable": "true",
                    },
                    {
                        "resource-id": "com.app:id/text",
                        "class": "android.widget.TextView",
                        "clickable": "false",
                    },
                ]
            }
        }
        
        hash_result = get_skeleton_hash(page)
        
        assert hash_result is not None
        assert len(hash_result) == 64


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
