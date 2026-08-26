(render {
  :on-click handle-click!
  :on-submit (comp audit user/submit-form!)
})
