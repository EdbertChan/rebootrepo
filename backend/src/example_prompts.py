from reboot.application import ExamplePrompt


example_prompts = [
    ExamplePrompt(
        title="Create a market",
        prompts=[
            "Create a prediction market: \"Will the demo pass today?\" "
            "that closes immediately.",
            "Show me my prediction market dashboard.",
        ],
    ),
    ExamplePrompt(
        title="Bet and resolve",
        prompts=[
            "Place a 25 credit YES bet on my newest open market.",
            "Resolve the market as YES and show me the audit log.",
        ],
    ),
]
