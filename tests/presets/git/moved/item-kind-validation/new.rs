#[test]
fn test_assets_items() {
    let ids = all_item_defs_expect();
    for item in ids.iter().map(|id| Item::new_from_asset_expect(id)) {
        match item.kind().as_ref() {
            ItemKind::Consumable { container, .. } => {
                let Some(container) = container else {
                    continue;
                };
                Item::new_from_item_definition_id(
                    container.as_ref(),
                    &AbilityMap::load().read(),
                    &MaterialStatManifest::load().read(),
                )
                .unwrap_or_else(|e| {
                    let id = item.item_definition_id();
                    panic!("error on {id:?}:\n{e:#?}");
                });
            },
            ItemKind::Special {
                effect: OpenEffect::PickRecipe(ids),
            } => {
                for recipe_id in ids {
                    Item::new_from_item_definition_id(
                        recipe_id.as_ref(),
                        &AbilityMap::load().read(),
                        &MaterialStatManifest::load().read(),
                    )
                    .unwrap_or_else(|e| {
                        let id = item.item_definition_id();
                        panic!("error on {id:?}: {e:#?}");
                    });
                }

                if ids.is_empty() {
                    let id = item.item_definition_id();
                    panic!("empty recipe list on {id:?}");
                }
            },
            ItemKind::TagExamples { item_ids: ids } => {
                for id in ids {
                    Item::new_from_asset_expect(id);
                }
            },
            ItemKind::RecipeGroup { .. } => {
                // TODO: validate
            },
            ItemKind::Quest
            | ItemKind::Tool { .. }
            | ItemKind::Lantern { .. }
            | ItemKind::Armor { .. }
            | ItemKind::Glider
            | ItemKind::ModularComponent { .. }
            | ItemKind::Utility { .. }
            | ItemKind::Ingredient { .. } => {},
        }
        drop(item)
    }
}
