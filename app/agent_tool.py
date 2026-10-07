"""外部エージェント（Hermes Agent / OpenClaw）への入り口。

ask_agent で時間のかかる作業を頼み、完了したら `$` 通知でアバターに結果を要約させる
（AIAvatarKit の OpenClawTool + use_invoke_queue。voice-avatar の実装を移植）。
エージェント本体のインストールはこのリポジトリの対象外。
"""
from aiavatar.sts.llm.tools.openclaw_tool import OpenClawTool
from aiavatar.sts.models import STSRequest

MAX_ANSWER_CHARS = 3000

KINDS = {
    "hermes": {"harness": "hermes", "label": "Hermes Agent"},
    "openclaw": {"harness": "openclaw", "label": "OpenClaw"},
}

DESCRIPTION = (
    "時間のかかる作業を、外部の作業エージェントに依頼する。ファイル操作、コード実行、長い調査、"
    "複数の手順が必要な作業など、会話の合間に終わらない作業だけに使う。"
    "天気や簡単な検索は自分のツールで済ませ、雑談や自分の知識で答えられる質問には使わない。"
    "依頼すると結果はあとで通知される。"
)


def create_tools(agent_cfg: dict, get_app, debug: bool = False) -> list:
    """agent_cfg: settings["tools"]["agent"]。get_app: AIAvatarWebSocketServer を返す関数（完了通知で使う）"""
    kind = KINDS[agent_cfg.get("kind", "hermes")]
    tool = OpenClawTool(
        openclaw_api_key=agent_cfg.get("api_key") or "none",
        openclaw_base_url=agent_cfg["base_url"].rstrip("/"),
        harness=kind["harness"],
        stream=kind["harness"] == "hermes",     # Hermes は進捗（使っているツール）をストリームで返す
        timeout=int(agent_cfg.get("timeout") or 600),
        name="ask_agent",
        spec={
            "type": "function",
            "function": {
                "name": "ask_agent",
                "description": DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "エージェントに渡す依頼文。ユーザーの要望を、前後の文脈を補って具体的に書く。"},
                    },
                    "required": ["query"],
                },
            },
        },
        immediate_message="依頼を受け付けた。完了したら結果が通知される。",
        debug=debug,
    )

    # on_completed は add_tool より前に登録する（add_tool は浅いコピーで登録するため）
    @tool.on_completed
    async def on_completed(result, metadata):
        answer = ((result or {}).get("answer") or "エラーで結果を取得できなかった。")[:MAX_ANSWER_CHARS]
        app = get_app()
        async for resp in app.sts.invoke(
            STSRequest(
                session_id=metadata["session_id"],
                user_id=metadata["user_id"],
                context_id=metadata["context_id"],
                text=(
                    f"$作業エージェント（{kind['label']}）から作業結果が返りました。次の内容を、話し言葉で短く要約して"
                    f"ユーザーに伝えてください。URL は読み上げないでください。\n\n{answer}"
                ),
                channel=metadata.get("channel"),
                wait_in_queue=True,
                skip_quick_response=True,
            )
        ):
            await app.handle_response(resp)

    check = tool.create_check_tool(
        name="check_agent_tasks",
        description="作業エージェントに依頼した作業の進み具合を確認する。ユーザーに進捗を聞かれたときに使う。",
    )
    return [tool, check]
