def render_order(order: dict[str, object]) -> str:
    normalized_items = normalize_items(order)

    lines: list[str] = []
    for item in normalized_items:
        lines.append(f"{item['quantity']}x {item['name']}")
    return "\n".join(lines)


def normalize_items(order: dict[str, object]) -> list[dict[str, object]]:
    normalized_items: list[dict[str, object]] = []
    for item in order["items"]:
        name = item["name"].strip()
        quantity = int(item.get("quantity", 1))
        normalized_items.append(
            {
                "name": name,
                "quantity": quantity,
            }
        )
    return normalized_items
