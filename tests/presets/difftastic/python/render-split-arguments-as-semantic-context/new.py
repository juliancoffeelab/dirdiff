def create_app(service, defaults, *, services):
    return service, defaults, services


def preset(service, defaults, git_service, difftastic_service):
    return create_app(
        service,
        defaults,
        services={"git": git_service, "difftastic": difftastic_service},
    )
