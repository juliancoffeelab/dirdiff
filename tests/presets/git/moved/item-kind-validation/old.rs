#[test]
fn test_assets_items() {
    let ids = all_item_defs_expect();
    for item in ids.iter().map(|id| Item::new_from_asset_expect(id)) {
        if let ItemKind::Consumable {
            container: Some(container),
            ..
        } = item.kind().as_ref()
        {
            Item::new_from_item_definition_id(
                container.as_ref(),
                &AbilityMap::load().read(),
                &MaterialStatManifest::load().read(),
            )
            .unwrap();
        }
        drop(item)
    }
}
