#[allow(clippy::result_unit_err)]
pub fn resolve_take_deposit(
    ctx: &mut NpcCtx,
    quest_id: QuestId,
    success: bool,
) -> Result<Option<(Arc<ItemDef>, u32)>, ()> {
    if let Some((outcome, kind)) = ctx.data.quests.get(quest_id).and_then(|q| {
        q.resolve(ctx.npc_id, success)
            .map(|outcome| (outcome, QuestEventKind::from_kind(&q.kind)))
    }) {
        ctx.controller.quest_events.push(OnQuestEvent {
            kind,
            event: QuestEvent::Resolved { success },
        });

        // ...take the deposit back into our own inventory...
        if let Some((item, amount)) = &outcome.deposit
            && let Some(npc_entity) =
                ctx.system_data.id_maps.rtsim_entity(ctx.npc_id)
        {
            ctx.controller.give_item(npc_entity, item, *amount);
        }
    }

    Ok(None)
}
