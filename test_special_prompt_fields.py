from special_opearte_contract import build_special_opearte, normalize_special_opearte


def test_manual_special_item_always_keeps_description_and_prompt():
    special = normalize_special_opearte({
        "operation1": [
            {
                "type": "key",
                "value": "font_size",
                "key_description": "字体大小",
                "step_prompt": "点击字体大小",
            }
        ]
    })

    assert special == {
        "operation1": [
            {
                "type": "key",
                "value": "font_size",
                "key_description": "字体大小",
                "step_prompt": "点击字体大小",
            }
        ]
    }


def test_missing_manual_prompt_fields_are_filled_instead_of_dropped():
    special = normalize_special_opearte({
        "operation1": [
            {
                "type": "text",
                "value": "字体大小",
            }
        ]
    })

    assert special["operation1"][0] == {
        "type": "text",
        "value": "字体大小",
        "key_description": "字体大小",
        "step_prompt": "字体大小",
    }


def test_recorded_special_item_uses_four_field_locator_schema():
    state = {
        "page_operations": [
            {
                "created_at": "2026-08-07T13:50:00",
                "operation_kind": "special_operate",
                "effect": "special_capture::font::step1",
                "target": {
                    "key": "font_size",
                    "key_description": "字体大小",
                    "step_prompt": "点击字体大小",
                },
            }
        ]
    }

    assert build_special_opearte(state) == {
        "operation1": [
            {
                "type": "key",
                "value": "font_size",
                "key_description": "字体大小",
                "step_prompt": "点击字体大小",
            }
        ]
    }
