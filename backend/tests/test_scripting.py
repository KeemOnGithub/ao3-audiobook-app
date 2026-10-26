"""Unit tests for Phase 2: LLM Script Deconstruction and Voice Architecture."""

import json
import httpx
import pytest

from backend.app.scripting.analyzer import LLMScriptAnalyzer
from backend.app.scripting.schemas import (
    CharacterUpdateSpec,
    ChunkAnalysisResult,
    ChunkParagraphInput,
    NewCharacterSpec,
    PreviousSegmentContext,
    RegistryCharacterContext,
    ScriptSegmentSpec,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_value", [None, "unexpected"])
async def test_provider_null_segment_character_metadata(metadata_value):
    from pydantic import ValidationError

    payload = {
        "new_characters": [],
        "character_updates": [],
        "segments": [{
            "paragraph_index": 0,
            "speaker_id": "narrator",
            "text": "The room was quiet.",
            "is_paragraph_end": True,
            "character_id": metadata_value,
            "voice_instruct": None,
            "reference_sample_text": None,
        }],
    }

    def handler(request):
        body = json.loads(request.content)
        system_prompt = body["messages"][0]["content"]
        from backend.app.scripting.prompts import CHUNK_ANALYSIS_JSON_SCHEMA
        assert json.dumps(CHUNK_ANALYSIS_JSON_SCHEMA["schema"]) in system_prompt
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(payload)}, "finish_reason": "stop",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        analyzer = LLMScriptAnalyzer(client=client, max_retries=0)
        if metadata_value is not None:
            with pytest.raises(ValidationError):
                await analyzer._call_qwen_flash_next(0, {}, 0.1)
        else:
            result = await analyzer.analyze_chunk(
                0, [ChunkParagraphInput(paragraph_index=0, text="The room was quiet.")], [],
            )
            assert len(result.segments) == 1
            assert result.segments[0].text == "The room was quiet."
            assert result.segments[0].speaker_id == "char_narrator"


def test_character_spec_id_normalization():
    """Verify character_id validator normalizes strings to char_<snake_case>."""
    char = NewCharacterSpec(
        character_id="Harry Potter!!",
        canonical_name="Harry Potter",
        aliases=["Harry", "The Boy Who Lived"],
        gender="male",
        voice_instruct="A young adult male voice with clear tone and earnest delivery.",
        reference_sample_text="We have to get to the library before curfew begins.",
    )
    assert char.character_id == "char_harry_potter"


def test_empty_paragraphs_returns_empty_result():
    analyzer = LLMScriptAnalyzer()
    import asyncio
    result = asyncio.run(analyzer.analyze_chunk(0, [], []))
    assert result.segments == []
    assert result.new_characters == []


def test_reconciliation_and_deduplication():
    """Test deduplication of new characters against existing registry."""
    analyzer = LLMScriptAnalyzer()

    existing = [
        RegistryCharacterContext(
            character_id="narrator",
            canonical_name="Narrator",
            aliases=[],
            gender="neutral",
        ),
        RegistryCharacterContext(
            character_id="char_hermione",
            canonical_name="Hermione Granger",
            aliases=["Hermione"],
            gender="female",
        ),
    ]

    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text='"We must be careful," Hermione whispered.'),
        ChunkParagraphInput(paragraph_index=1, text="The corridor was pitch black."),
    ]

    # LLM erroneously proposes Hermione Granger as a new character
    raw_result = ChunkAnalysisResult(
        new_characters=[
            NewCharacterSpec(
                character_id="char_hermione_granger",
                canonical_name="Hermione Granger",
                aliases=["Granger"],
                gender="female",
                voice_instruct="A young adult female soprano with articulate and brisk delivery.",
                reference_sample_text="The library books have all the answers we will ever need.",
            )
        ],
        character_updates=[],
        segments=[
            ScriptSegmentSpec(
                segment_order=0,
                paragraph_index=0,
                speaker_id="char_hermione_granger",
                text='"We must be careful,"',
                is_paragraph_end=False,
            ),
            ScriptSegmentSpec(
                segment_order=1,
                paragraph_index=0,
                speaker_id="narrator",
                text="Hermione whispered.",
                is_paragraph_end=True,
            ),
            ScriptSegmentSpec(
                segment_order=2,
                paragraph_index=1,
                speaker_id="narrator",
                text="The corridor was pitch black.",
                is_paragraph_end=True,
            ),
        ],
    )

    reconciled = analyzer._reconcile_and_validate(raw_result, paragraphs, existing)

    # char_hermione_granger must be deduped out of new_characters and merged into updates
    assert len(reconciled.new_characters) == 0
    assert len(reconciled.character_updates) == 1
    assert reconciled.character_updates[0].character_id == "char_hermione"

    # Segment speaker_id must be remapped to existing char_hermione
    assert reconciled.segments[0].speaker_id == "char_hermione"
    assert reconciled.segments[1].speaker_id == "char_narrator"
    assert reconciled.segments[2].speaker_id == "char_narrator"

    # Verify is_paragraph_end flags
    assert reconciled.segments[0].is_paragraph_end is False
    assert reconciled.segments[1].is_paragraph_end is True
    assert reconciled.segments[2].is_paragraph_end is True


def test_orphan_speaker_recovery():
    """Verify that an unknown speaker_id is safely auto-synthesized so foreign keys don't fail."""
    analyzer = LLMScriptAnalyzer()
    existing = [
        RegistryCharacterContext(
            character_id="char_narrator",
            canonical_name="Narrator",
            aliases=[],
            gender="neutral",
        )
    ]
    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text='"Stop right there!" shouted the guard.'),
    ]

    raw_result = ChunkAnalysisResult(
        new_characters=[],
        character_updates=[],
        segments=[
            ScriptSegmentSpec(
                segment_order=0,
                paragraph_index=0,
                speaker_id="char_unknown_guard",  # Not in existing or new_characters
                text='"Stop right there!"',
                is_paragraph_end=False,
            ),
            ScriptSegmentSpec(
                segment_order=1,
                paragraph_index=0,
                speaker_id="narrator",
                text="shouted the guard.",
                is_paragraph_end=True,
            ),
        ],
    )

    reconciled = analyzer._reconcile_and_validate(raw_result, paragraphs, existing)

    # An auto-synthesized fallback character should have been created
    assert len(reconciled.new_characters) == 1
    fallback_char = reconciled.new_characters[0]
    assert fallback_char.character_id == "char_unknown_guard"
    assert fallback_char.gender == "neutral"
    assert len(fallback_char.voice_instruct) > 0


def test_deterministic_fallback_generation():
    """Verify deterministic fallback preserves all prose as narrator segments."""
    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text="First line of prose."),
        ChunkParagraphInput(paragraph_index=1, text="Second line of prose."),
    ]
    fallback = LLMScriptAnalyzer._build_deterministic_fallback(paragraphs)

    assert len(fallback.segments) == 2
    assert fallback.new_characters == []
    assert fallback.segments[0].speaker_id == "char_narrator"
    assert fallback.segments[0].text == "First line of prose."
    assert fallback.segments[0].is_paragraph_end is True
    assert fallback.segments[1].speaker_id == "char_narrator"
    assert fallback.segments[1].text == "Second line of prose."
    assert fallback.segments[1].is_paragraph_end is True


@pytest.mark.asyncio
async def test_analyze_chunk_with_mock_llm():
    """Verify end-to-end analyze_chunk with mock HTTP transport and <think> tag removal."""
    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": """<think>
Some reasoning thoughts that must be stripped out.
</think>
{
  "new_characters": [
    {
      "character_id": "char_ron",
      "canonical_name": "Ron Weasley",
      "aliases": ["Ron"],
      "gender": "male",
      "voice_instruct": "Young adult male mid baritone, earnest and lively delivery.",
      "reference_sample_text": "Blimey, we are going to be in huge trouble if we get caught."
    }
  ],
  "character_updates": [],
  "segments": [
    {
      "segment_order": 0,
      "paragraph_index": 0,
      "speaker_id": "char_ron",
      "text": "\\\"Blimey!\\\"",
      "is_paragraph_end": false
    },
    {
      "segment_order": 1,
      "paragraph_index": 0,
      "speaker_id": "narrator",
      "text": "Ron gasped aloud.",
      "is_paragraph_end": true
    }
  ]
}"""
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "EMPTY"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=mock_llm_response, request=request)

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    analyzer = LLMScriptAnalyzer(client=mock_client)

    existing = [
        RegistryCharacterContext(
            character_id="narrator",
            canonical_name="Narrator",
            aliases=[],
            gender="neutral",
        )
    ]
    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text='"Blimey!" Ron gasped aloud.'),
    ]

    result = await analyzer.analyze_chunk(
        chunk_index=0,
        paragraphs=paragraphs,
        existing_characters=existing,
    )

    assert len(result.new_characters) == 1
    assert result.new_characters[0].character_id == "char_ron"
    assert len(result.segments) == 2
    assert result.segments[0].text == '"Blimey!"'
    assert result.segments[0].speaker_id == "char_ron"
    assert result.segments[1].text == " Ron gasped aloud."
    assert result.segments[1].is_paragraph_end is True


@pytest.mark.asyncio
async def test_analyze_chunk_with_markdown_fences():
    """Verify analyze_chunk strips markdown code fences from OpenAI-compatible output."""
    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": """```json
{
  "new_characters": [],
  "character_updates": [],
  "segments": [
    {
      "segment_order": 0,
      "paragraph_index": 0,
      "speaker_id": "narrator",
      "text": "The wind howled outside.",
      "is_paragraph_end": true
    }
  ]
}
```"""
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_llm_response, request=request)

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    analyzer = LLMScriptAnalyzer(client=mock_client, model="deepseek-v4-flash")

    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text="The wind howled outside."),
    ]

    result = await analyzer.analyze_chunk(
        chunk_index=0,
        paragraphs=paragraphs,
        existing_characters=[],
    )

    assert len(result.segments) == 1
    assert result.segments[0].text == "The wind howled outside."
    assert result.segments[0].speaker_id == "char_narrator"


@pytest.mark.asyncio
async def test_analyze_chunk_normalizes_segment_index_alias():
    """Accept the segment_index alias emitted by some compatible providers."""
    response = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({
                "new_characters": [],
                "character_updates": [],
                "segments": [{
                    "segment_index": 0,
                    "paragraph_index": 0,
                    "speaker_id": "narrator",
                    "text": "A complete sentence.",
                    "is_paragraph_end": True,
                }],
            })},
        }],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        analyzer = LLMScriptAnalyzer(client=client, max_retries=0)
        result = await analyzer.analyze_chunk(
            chunk_index=0,
            paragraphs=[ChunkParagraphInput(paragraph_index=0, text="A complete sentence.")],
            existing_characters=[],
        )

    assert result.segments[0].segment_order == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_budget,explicit_budget,expected_budget",
    [
        (None, None, 8192),
        ("16384", None, 16384),
        ("2048", None, 2048),
        ("2048", 12288, 12288),
    ],
)
async def test_llm_request_uses_configured_token_budget(monkeypatch, env_budget, explicit_budget, expected_budget):
    if env_budget is None:
        monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("LLM_MAX_TOKENS", env_budget)
    requests = []
    result = LLMScriptAnalyzer._build_deterministic_fallback(
        [ChunkParagraphInput(paragraph_index=0, text="Some prose text.")]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": result.model_dump_json()}}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        analyzer = LLMScriptAnalyzer(client=client, max_tokens=explicit_budget, max_retries=0)
        await analyzer.analyze_chunk(
            chunk_index=0,
            paragraphs=[ChunkParagraphInput(paragraph_index=0, text="Some prose text.")],
            existing_characters=[],
        )

    assert [request["max_tokens"] for request in requests] == [expected_budget]


@pytest.mark.parametrize("budget", [0, -1])
def test_llm_token_budget_must_be_positive(monkeypatch, budget):
    monkeypatch.setenv("LLM_MAX_TOKENS", str(budget))
    with pytest.raises(ValueError, match="greater than zero"):
        LLMScriptAnalyzer()
    with pytest.raises(ValueError, match="greater than zero"):
        LLMScriptAnalyzer(max_tokens=budget)


@pytest.mark.asyncio
async def test_llm_finish_reason_length_raises_truncated():
    """Verify provider response with finish_reason='length' raises LLMOutputTruncated."""
    from backend.app.scripting.analyzer import LLMOutputTruncated

    mock_llm_response = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": '{"new_characters":[],"character_updates":[],"segments":[{"segment_order":0,"paragraph_index":0,"speaker_id":"narrator","text":"Half sentence...',
                },
            }
        ],
        "usage": {"completion_tokens": 2048, "total_tokens": 2200},
    }

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=mock_llm_response, request=request)

    transport = httpx.MockTransport(handler)
    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text="A whole paragraph text."),
    ]

    async with httpx.AsyncClient(transport=transport) as client:
        analyzer = LLMScriptAnalyzer(client=client, max_tokens=2048)
        with pytest.raises(LLMOutputTruncated) as excinfo:
            await analyzer.analyze_chunk(
                chunk_index=3,
                paragraphs=paragraphs,
                existing_characters=[],
            )
    assert excinfo.value.finish_reason == "length"
    assert excinfo.value.chunk_index == 3
    assert excinfo.value.detail["usage"] == mock_llm_response["usage"]
    assert "max_tokens=2048" in str(excinfo.value)
    assert "Increase LLM_MAX_TOKENS" in str(excinfo.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_exhausted_retries_fail_closed():
    """Verify that when attempts exhaust without success, analyzer raises RuntimeError rather than falling back silently."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server exploded"}, request=request)

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    analyzer = LLMScriptAnalyzer(client=mock_client, max_retries=1)

    paragraphs = [
        ChunkParagraphInput(paragraph_index=0, text="Some prose text."),
    ]

    with pytest.raises(RuntimeError, match="LLM_ANALYSIS_FAILED"):
        await analyzer.analyze_chunk(
            chunk_index=0,
            paragraphs=paragraphs,
            existing_characters=[],
        )


def reconcile_text(source, texts, indices=None):
    paragraphs = [ChunkParagraphInput(paragraph_index=index, text=text) for index, text in source]
    result = ChunkAnalysisResult(new_characters=[], character_updates=[], segments=[
        ScriptSegmentSpec(segment_order=i, paragraph_index=(indices or [source[0][0]] * len(texts))[i],
                          speaker_id="narrator", text=text, is_paragraph_end=True)
        for i, text in enumerate(texts)
    ])
    return LLMScriptAnalyzer()._reconcile_and_validate(result, paragraphs, [])


@pytest.mark.parametrize("source,texts", [
    ('"Wait," she said, "not yet."', ['"Wait,"', 'she said,', '"not yet."']),
    ('"Did he say \'tomorrow\'?" she asked.', ['"Did he say \'tomorrow\'?"', 'she asked.']),
    ('"I don\'t know..."', ['"I don\'t know..."']),
    ('*I should leave,* she thought.', ['*I should leave,*', 'she thought.']),
    ('"First sentence. Second sentence."', ['"First sentence.', 'Second sentence."']),
    ('A--B; [C]: **D**!', ['A--B;', '[C]:', '**D**!']),
    ('A  B\tC.', ['A', 'B\tC.']),
    ('"Wait," she said.', ['  "Wait,"  ', '', '  she said. ']),
])
def test_canonical_fidelity_and_boundary_whitespace(source, texts):
    result = reconcile_text([(0, source)], texts)
    assert ''.join(segment.text for segment in result.segments) == source
    assert [segment.segment_order for segment in result.segments] == list(range(len(result.segments)))
    assert [segment.is_paragraph_end for segment in result.segments] == [False] * (len(result.segments) - 1) + [True]


@pytest.mark.parametrize("source,texts", [
    ('"Hello!"', ['Hello!']),
    ('Hello!', ['Hello!!']),
    ('Hello!', ['hello!']),
    ('A part.', ['Apart.']),
    ('Apart.', ['A part.']),
    ('Apart.', ['A', 'part.']),
    ("don't", ['don', "'t"]),
    ("don't", ["don'", 't']),
    ('A  part.', ['A part.']),
    ('First. Second.', ['Second.', 'First.']),
    ('Again. Again.', ['Again.']),
    ('"Wait,"', ['"Wait."']),
    ('**Hello**', ['Hello']),
    ('Hello!', ['Hi!']),
    ('Hello!', ['Hello!', 'More.']),
    ('"Hello!"', ['"', 'Hello!"']),
    ('"Hello!"', ['"Hello!', '"']),
    ('---', ['---']),
    ('Hello!', ['  ']),
])
def test_fidelity_rejects_source_changes_and_punctuation_only(source, texts):
    with pytest.raises(ValueError):
        reconcile_text([(0, source)], texts)


def test_actual_same_index_fragments_are_sequential_and_complete():
    source = [(4, '"First sentence.'), (4, 'Second sentence."'), (7, 'Afterward.')]
    result = reconcile_text(source, ['"First sentence.', 'Second sentence."', 'Afterward.'], [4, 4, 7])
    assert [segment.text for segment in result.segments] == ['"First sentence.', ' Second sentence."', 'Afterward.']
    assert [segment.is_paragraph_end for segment in result.segments] == [False, True, True]
    combined = reconcile_text(source, ['"First sentence. Second sentence."', 'Afterward.'], [4, 7])
    assert len(combined.segments) == 2
    for texts, indices in [(['Second sentence."', '"First sentence.', 'Afterward.'], [4, 4, 7]),
                           (['"First sentence.', 'Afterward.'], [4, 7]),
                           (['"First sentence. Second sentence."', 'Afterward.'], [99, 7])]:
        with pytest.raises(ValueError):
            reconcile_text(source, texts, indices)


def test_repeated_nonconsecutive_indices_do_not_allow_reordering():
    source = [(4, 'One.'), (5, 'Two.'), (4, 'Three.')]
    assert len(reconcile_text(source, ['One.', 'Two.', 'Three.'], [4, 5, 4]).segments) == 3
    with pytest.raises(ValueError):
        reconcile_text(source, ['One.', 'Three.', 'Two.'], [4, 4, 5])


def test_unbalanced_chunk_quotes_are_preserved_without_wrapping():
    assert reconcile_text([(4, '"Start.')], ['"Start.']).segments[0].text == '"Start.'
    assert reconcile_text([(4, 'Finish."')], ['Finish."']).segments[0].text == 'Finish."'


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_fidelity_failures_use_bounded_retries(monkeypatch, recover):
    calls = []
    analyzer = LLMScriptAnalyzer(max_retries=2)

    async def mock_call(**kwargs):
        calls.append(kwargs)
        return ChunkAnalysisResult(new_characters=[], character_updates=[], segments=[
            ScriptSegmentSpec(segment_order=0, paragraph_index=3, speaker_id="narrator",
                              text='"Hello!"' if recover and len(calls) == 2 else 'Hello!',
                              is_paragraph_end=True)
        ])

    monkeypatch.setattr(analyzer, '_call_qwen_flash_next', mock_call)
    paragraphs = [ChunkParagraphInput(paragraph_index=3, text='"Hello!"')]
    if recover:
        result = await analyzer.analyze_chunk(9, paragraphs, [])
        assert result.segments[0].text == '"Hello!"'
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError, match='LLM_ANALYSIS_FAILED'):
            await analyzer.analyze_chunk(9, paragraphs, [])
        assert len(calls) == 3


