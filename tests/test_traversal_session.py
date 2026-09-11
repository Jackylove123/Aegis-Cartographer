"""
TraversalSession 单元测试

验证 DFS 遍历核心逻辑
"""

import os
import sys

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from aegis_cartographer.traversal_session import (
    ElementInfo,
    TraversalFrame,
    TraversalSession,
)


def test_element_info():
    """测试元素信息"""
    elem = ElementInfo(
        element_id="btn_1",
        text_content="按钮",
    )
    assert elem.element_id == "btn_1"
    assert not elem.is_explored
    print("✓ ElementInfo 创建成功")


def test_traversal_frame():
    """测试遍历帧"""
    elements = [
        ElementInfo(element_id="btn_1", text_content="按钮1"),
        ElementInfo(element_id="btn_2", text_content="按钮2"),
    ]

    frame = TraversalFrame(
        state_id="state_1",
        semantic_name="测试页面",
        elements=elements,
    )

    # 测试获取当前元素
    current = frame.get_current_element()
    assert current is not None
    assert current.element_id == "btn_1"

    # 测试前进
    elem = frame.advance()
    assert elem is not None
    assert elem.element_id == "btn_1"

    # 测试未探索元素
    assert frame.has_unexplored_elements()

    print("✓ TraversalFrame 测试通过")


def test_traversal_session():
    """测试遍历会话"""
    session = TraversalSession(
        session_id="test_session",
        project_root="/tmp/test",
        target_package="com.test",
    )

    # 测试启动
    initial_elements = [
        {"element_id": "btn_1", "text_content": "按钮1"},
        {"element_id": "btn_2", "text_content": "按钮2"},
    ]

    result = session.start("state_1", initial_elements)
    assert result["success"]
    assert session.status.value == "visiting"
    assert len(session.stack) == 1

    # 测试获取下一步动作
    result = session.get_next_action("state_1", initial_elements)
    assert result["action"] == "CLICK"
    assert result["element_id"] == "btn_1"

    # 测试上报执行
    result = session.report_execution({
        "executed_action": "CLICK",
        "executed_element_id": "btn_1",
        "resulting_state_id": "state_2",
        "success": True,
    })
    assert result["success"]

    # 验证元素被标记为已探索
    assert session.stack[0].elements[0].is_explored

    # 测试新状态处理
    new_elements = [{"element_id": "btn_3", "text_content": "按钮3"}]
    result = session.get_next_action("state_2", new_elements)
    assert result["action"] == "CLICK"
    assert result["element_id"] == "btn_3"
    assert len(session.stack) == 2  # 新页面入栈

    # 测试回溯
    result = session.get_next_action("state_1", initial_elements)
    assert result["action"] == "CLICK"
    assert result["element_id"] == "btn_2"  # 下一个未探索元素

    # 标记所有元素已探索，测试回溯指令
    session.stack[0].elements[0].is_explored = True
    session.stack[0].elements[1].is_explored = True
    result = session.get_next_action("state_1", initial_elements)
    assert result["action"] == "BACK"

    print("✓ TraversalSession 测试通过")


def test_dfs_behavior():
    """测试 DFS 深度优先行为"""
    session = TraversalSession(
        session_id="test_dfs",
        project_root="/tmp/test",
    )

    # 页面1：有2个按钮
    page1_elements = [
        {"element_id": "btn_a", "text_content": "进入页面2"},
        {"element_id": "btn_b", "text_content": "进入页面3"},
    ]

    session.start("page1", page1_elements)

    # 第一次调用：返回第一个按钮
    result = session.get_next_action("page1", page1_elements)
    assert result["element_id"] == "btn_a"
    assert len(session.stack) == 1

    # 模拟点击 btn_a 进入页面2
    session.report_execution({"executed_action": "CLICK", "executed_element_id": "btn_a", "resulting_state_id": "page2", "success": True})

    page2_elements = [{"element_id": "btn_c", "text_content": "返回"}]

    # 进入页面2
    result = session.get_next_action("page2", page2_elements)
    assert result["action"] == "CLICK"
    assert result["element_id"] == "btn_c"
    assert len(session.stack) == 2  # 深度增加

    # 模拟页面2所有元素探索完成，回溯
    session.stack[1].elements[0].is_explored = True
    result = session.get_next_action("page2", page2_elements)
    assert result["action"] == "BACK"

    # 回溯后应该在页面1，返回第二个按钮
    session.report_execution({"executed_action": "BACK", "resulting_state_id": "page1", "success": True})
    result = session.get_next_action("page1", page1_elements)
    assert result["action"] == "CLICK"
    assert result["element_id"] == "btn_b"  # 第二个按钮
    assert len(session.stack) == 1  # 回到页面1

    print("✓ DFS 深度优先行为测试通过")


if __name__ == "__main__":
    test_element_info()
    test_traversal_frame()
    test_traversal_session()
    test_dfs_behavior()

    print("\n" + "="*50)
    print("所有测试通过！✓")
    print("="*50)
