def node(
    node_type,
    key="",
    text="",
    bounds="[0,0][0,0]",
    clickable=False,
    children=None,
    **extra,
):
    attrs = {
        "type": node_type,
        "key": key,
        "text": text,
        "bounds": bounds,
        "visible": "true",
        "enabled": "true",
    }
    if clickable:
        attrs["clickable"] = "true"
    attrs.update({key: value for key, value in extra.items() if value not in (None, "")})
    return {"attributes": attrs, "children": children or []}


def themes_tree():
    return node("Root", bounds="[0,0][1080,2400]", children=[
        node("NavDestination", key="settings.themes", bounds="[0,0][1080,2400]", children=[
            node("TitleBar", bounds="[0,0][1080,180]", children=[
                node("Button", key="nav.back", text="返回", bounds="[24,64][112,152]", clickable=True),
                node("Text", key="page.title_id", text="主题", bounds="[128,72][360,144]"),
            ]),
            node("NavDestinationContent", bounds="[0,180][1080,2400]", children=[
                node(
                    "Column",
                    key="theme.current.card",
                    text="晨雾主题",
                    bounds="[96,260][984,1240]",
                    clickable=True,
                    children=[
                        node("Text", text="晨雾主题", bounds="[156,320][520,388]"),
                        node("Text", text="左滑切换下一个主题", bounds="[156,1070][620,1120]"),
                        node("Text", text="上滑删除当前主题", bounds="[156,1130][580,1180]"),
                    ],
                ),
                node(
                    "ListItem",
                    key="theme.store.entry",
                    text="主题商店",
                    bounds="[36,1340][1044,1476]",
                    clickable=True,
                    children=[
                        node("Text", text="主题商店", bounds="[64,1376][360,1436]"),
                    ],
                ),
            ]),
        ]),
    ])
