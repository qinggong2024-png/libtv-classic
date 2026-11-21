import re

import pytest
from libtv_agent.services.deep_video_prompts import compile_recreation_prompt


@pytest.mark.parametrize("count", range(1, 6))
@pytest.mark.parametrize("background", [False, True])
def test_reference_bindings_and_background_precedence(count, background):
    prompt = compile_recreation_prompt(count, has_background=background, requirement="Keep logo")
    numbers = {int(number) for number in re.findall(r"@Image (\d+)", prompt)}
    assert numbers == set(range(1, count + int(background) + 1))
    assert "Keep logo" in prompt
    assert "dark depth-map regions as a black backdrop" in prompt
    assert "Source-background objects are not required unless" in prompt
    assert ("background visible in @Image 1" in prompt) is (not background)
    assert ("hair, and the final background environment" in prompt) is (not background)
    if background:
        assert f"@Image {count + 1} defines the final background environment" in prompt
        assert "taking precedence over the backgrounds in all other references" in prompt
    if count == 1:
        assert "Use the outfit and product visible in @Image 1" in prompt
    else:
        assert "Replace the clothing shown in @Image 1 with this garment" in prompt
    if count > 2:
        assert "provide additional target details as specified" in prompt


def test_custom_target_roles_remain_available():
    requirement = "Use @Image 2 for the bag; keep the outfit in @Image 1"
    prompt = compile_recreation_prompt(2, has_background=False, requirement=requirement)
    assert "follow that assignment" in prompt
    assert prompt.endswith(requirement)


def test_empty_target_rejected():
    with pytest.raises(ValueError):
        compile_recreation_prompt(0, has_background=False, requirement="")
