"""
简化的 DFS 遍历逻辑测试

不依赖完整模块，只测试核心算法
"""


class ElementInfo:
    def __init__(self, element_id, text_content):
        self.element_id = element_id
        self.text_content = text_content
        self.is_explored = False


class TraversalFrame:
    def __init__(self, state_id, semantic_name, elements):
        self.state_id = state_id
        self.semantic_name = semantic_name
        self.elements = elements
        self.current_index = 0
        self.visited = False

    def get_next_unexplored_element(self):
        for i in range(self.current_index, len(self.elements)):
            if not self.elements[i].is_explored:
                self.current_index = i
                return self.elements[i]
        return None


def test_dfs_logic():
    """测试 DFS 核心逻辑"""
    print("测试 DFS 深度优先遍历逻辑...")

    # 模拟页面结构：
    # 页面1 -> 页面2 -> 页面3 (叶子)
    #       -> 页面4 (叶子)
    stack = []
    visited = set()

    # 页面1：2个按钮
    page1_elements = [
        ElementInfo("btn_a", "进入页面2"),
        ElementInfo("btn_b", "进入页面4"),
    ]

    # 进入页面1
    stack.append(TraversalFrame("page1", "页面1", page1_elements))
    visited.add("page1")

    # 第一次调用：返回 btn_a
    frame = stack[-1]
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_a"
    print("  ✓ 页面1: 返回 btn_a (进入页面2)")
    # 标记 btn_a 已探索
    elem.is_explored = True

    # 模拟点击 btn_a，进入页面2
    page2_elements = [ElementInfo("btn_c", "进入页面3")]
    stack.append(TraversalFrame("page2", "页面2", page2_elements))
    visited.add("page2")

    # 在页面2：返回 btn_c
    frame = stack[-1]
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_c"
    print("  ✓ 页面2: 返回 btn_c (进入页面3)")
    # 标记 btn_c 已探索
    elem.is_explored = True

    # 模拟点击 btn_c，进入页面3 (叶子)
    page3_elements = []  # 没有可点击元素
    stack.append(TraversalFrame("page3", "页面3", page3_elements))
    visited.add("page3")

    # 页面3没有元素，返回 BACK
    frame = stack[-1]
    elem = frame.get_next_unexplored_element()
    assert elem is None
    print("  ✓ 页面3: 没有元素，准备回溯")

    # 回溯：弹出页面3
    stack.pop()
    # 回溯：弹出页面2 (所有元素已探索)
    stack.pop()

    # 回到页面1，应该返回 btn_b
    frame = stack[-1]
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_b"
    print("  ✓ 回到页面1: 返回 btn_b (进入页面4)")

    # 模拟点击 btn_b，进入页面4
    page4_elements = [ElementInfo("btn_d", "完成")]
    stack.append(TraversalFrame("page4", "页面4", page4_elements))
    visited.add("page4")

    # 验证遍历顺序：page1 -> page2 -> page3 -> back -> back -> page1 -> page4
    # 这是标准的 DFS 深度优先遍历
    print(f"  ✓ 栈深度: {len(stack)} (应该在页面4)")
    assert len(stack) == 2  # page1 + page4

    print("\n✓ DFS 深度优先逻辑测试通过！")


def test_element_exploration_order():
    """测试元素探索顺序"""
    print("\n测试元素探索顺序...")

    elements = [
        ElementInfo("btn_1", "按钮1"),
        ElementInfo("btn_2", "按钮2"),
        ElementInfo("btn_3", "按钮3"),
    ]

    frame = TraversalFrame("test", "测试页", elements)

    # 第一次：btn_1
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_1"
    print(f"  ✓ 第1次: {elem.element_id}")

    # 标记已探索
    elem.is_explored = True

    # 第二次：btn_2
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_2"
    print(f"  ✓ 第2次: {elem.element_id}")

    # 标记已探索
    elem.is_explored = True

    # 第三次：btn_3
    elem = frame.get_next_unexplored_element()
    assert elem.element_id == "btn_3"
    print(f"  ✓ 第3次: {elem.element_id}")

    # 标记已探索
    elem.is_explored = True

    # 第四次：无
    elem = frame.get_next_unexplored_element()
    assert elem is None
    print("  ✓ 所有元素已探索")

    print("\n✓ 元素探索顺序测试通过！")


if __name__ == "__main__":
    test_dfs_logic()
    test_element_exploration_order()

    print("\n" + "="*50)
    print("所有简化测试通过！✓")
    print("DFS 核心逻辑验证正确")
    print("="*50)
