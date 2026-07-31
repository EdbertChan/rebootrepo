from reboot.application import ExamplePrompt


example_prompts = [
    ExamplePrompt(
        title="Plan today",
        prompts=[
            "Create a task called \"Confirm venue\" with the note "
            "\"Email the event space before noon.\"",
            "Create another task called \"Send invites\" and then show "
            "me my todo board.",
        ],
    ),
    ExamplePrompt(
        title="Clean up the list",
        prompts=[
            "List my current tasks.",
            "Mark the venue task complete.",
            "Show me the todo board again.",
        ],
    ),
]
