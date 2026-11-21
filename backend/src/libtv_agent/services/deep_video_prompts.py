"""Compile reference roles without mixing motion and scene appearance."""

PROMPT_VERSION = "deep-video-v2"


def compile_recreation_prompt(
    reference_count: int, *, has_background: bool, requirement: str,
) -> str:
    if reference_count < 1:
        raise ValueError("At least one target reference is required")
    identity = "@Image 1 defines the target person's identity, face, hair"
    identity += "." if has_background else ", and the final background environment."
    roles = [identity]
    if reference_count == 1:
        roles.append("Use the outfit and product visible in @Image 1.")
    else:
        roles.append(
            "@Image 2 defines the target garment only: its design, silhouette, colors, "
            "fabric, pattern, logos, and visible text. Ignore the background and any "
            "person shown in @Image 2. Replace the clothing shown in @Image 1 with this garment. "
            "If the additional requirements explicitly assign a different product or role "
            "to a target reference, follow that assignment."
        )
    if reference_count > 2:
        roles.append(
            ", ".join(f"@Image {index}" for index in range(3, reference_count + 1))
            + " provide additional target details as specified in the additional requirements; "
            "do not use their backgrounds or change the target identity."
        )
    background = (
        f"@Image {reference_count + 1} defines the final background environment, taking "
        "precedence over the backgrounds in all other references. Use it for scene "
        "appearance and lighting, while adapting perspective to the camera movement."
        if has_background else
        "Build a coherent scene based on the background visible in @Image 1 and maintain "
        "it consistently across shots. Adapt the scene perspective to each camera view."
    )
    sections = [
        "Generate a photorealistic, full-color video using the supplied grayscale depth "
        "video as a motion and framing reference.",
        "REFERENCE ROLES\n" + "\n".join(roles),
        "MOTION AND CAMERA\nFollow the depth video's shot order, action sequence, body poses, "
        "movement timing, subject framing, and camera movement. Depth values indicate "
        "distance only; they do not define final colors, lighting, or background appearance.",
        "BACKGROUND\n" + background + "\nDo not reproduce the source video's background or "
        "treat dark depth-map regions as a black backdrop. Source-background objects are "
        "not required unless needed for the person's physical interaction.",
        "CONSISTENCY AND REALISM\nMaintain the same target identity, hair, and garment "
        "throughout all shots. Preserve garment details as consistently as possible during "
        "movement. Match the person and garment to the target environment with coherent "
        "lighting, realistic scale, contact shadows, and natural occlusion.",
    ]
    if requirement.strip():
        sections.append("ADDITIONAL REQUIREMENTS\n" + requirement.strip())
    return "\n\n".join(sections)
