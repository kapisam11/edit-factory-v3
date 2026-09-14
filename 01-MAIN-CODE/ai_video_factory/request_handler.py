"""High-level request handler: routes user requests through the entire AI Video Factory pipeline.

This is the main user-facing API that:
1. Validates requests using the knowledge base
2. Processes requests with topic expertise and trending knowledge
3. Generates packages with learning feedback
4. Handles errors gracefully
"""
from typing import Dict, Tuple, Optional, Any
from pathlib import Path
import json
import os

from .knowledge import RequestProcessor, KnowledgeBase
from .plan import make_idea_with_knowledge
from .factory import create_package


class AIVideoFactoryRequest:
    """Main API: process any user request and generate video edits."""

    def __init__(self, output_root: str = "output"):
        self.processor = RequestProcessor()
        self.kb = self.processor.kb
        self.output_root = output_root

    def create_edit(
        self,
        topic: str,
        request: str,
        target_seconds: float = 45.0,
        thumbnail_subject: Optional[str] = None,
        use_groq: bool = False,
        groq_api_key: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Create a video edit from a user request.

        Args:
            topic: The topic/game/subject (Minecraft, Roblox, COD, etc.)
            request: What to create (e.g., "make a betrayal edit", "create a rare item showcase")
            target_seconds: Video duration (30-60s)
            thumbnail_subject: Optional specific thumbnail subject
            use_groq: Whether to use Groq API for enrichment
            groq_api_key: Groq API key if using

        Returns:
            (success: bool, message: str, package_dir: str or None)
        """
        # 1. Validate request
        success, message, context = self.processor.process_request(topic, request)

        if not success:
            return False, message, None

        # 2. Get expertise and trending
        expertise = context.get("expertise", {})
        trending = context.get("trending", {})
        tone = context.get("tone", "engaging")

        # 3. Build research summary from knowledge
        summary = self._build_summary(topic, request, expertise, tone)
        summary["target_total_seconds"] = str(target_seconds)

        # 4. Generate idea with knowledge
        try:
            idea = make_idea_with_knowledge(summary, topic, expertise, trending)

            # Log for learning
            self.kb.log_request(topic, request, True, f"Generated idea: {idea.get('hook', 'N/A')}")
            self.kb.save_all()

        except Exception as e:
            error_msg = f"Failed to generate idea: {str(e)}"
            self.processor.deny_request(topic, request, error_msg)
            return False, f"Sorry, I couldn't generate an edit for that. {error_msg}", None

        # 5. Create package
        try:
            pkg_dir = create_package(
                topic=topic,
                out_root=self.output_root,
                thumbnail_subject=thumbnail_subject or self._infer_thumbnail(request),
                use_groq=use_groq,
                groq_api_key=groq_api_key,
                target_total_seconds=target_seconds,
            )

            # 6. Attach knowledge context to package
            self._attach_knowledge_context(pkg_dir, idea, context, request)

            return True, f"Successfully created {topic} edit: {request}\nPackage: {pkg_dir}", pkg_dir

        except Exception as e:
            error_msg = f"Package creation failed: {str(e)}"
            self.processor.deny_request(topic, request, error_msg)
            return False, f"Sorry, I couldn't create the package. {error_msg}", None

    def _build_summary(self, topic: str, request: str, expertise: Dict, tone: str) -> Dict[str, str]:
        """Build a research summary from knowledge and request."""
        # Parse request for intent
        request_lower = request.lower()

        # Determine content type from request
        content_type = "Story"
        if "challenge" in request_lower or "guide" in request_lower:
            content_type = "Guide"
        elif "showcase" in request_lower or "highlight" in request_lower:
            content_type = "Showcase"
        elif "fails" in request_lower or "funny" in request_lower:
            content_type = "Comedy"
        elif "rare" in request_lower or "secret" in request_lower:
            content_type = "Discovery"

        # Build summary
        summary = {
            "topic": topic,
            "request": request,
            "content_type": content_type,
            "emotion": tone,
            "viral_title": self._generate_title(topic, request, expertise),
            "strongest_angle": expertise.get("best_hooks", ["dramatic moment"])[0],
            "why_care": self._generate_why_care(topic, expertise),
            "main_conflict": self._generate_conflict(topic, request, expertise),
            "who_what": f"In {topic}: {request}",
        }

        return summary

    def _generate_title(self, topic: str, request: str, expertise: Dict) -> str:
        """Generate a viral title from request and expertise."""
        key_elements = expertise.get("key_elements", [])
        if key_elements:
            return f"{request} - {key_elements[0]} in {topic}"
        return f"{request} - {topic}"

    def _generate_why_care(self, topic: str, expertise: Dict) -> str:
        """Generate why viewers should care."""
        trending = expertise.get("trending_now", ["something"])
        return f"This is what {topic} players are obsessed with right now: {trending[0]}"

    def _generate_conflict(self, topic: str, request: str, expertise: Dict) -> str:
        """Generate main conflict/stakes."""
        return f"Everyone knows about {request}, but what happened next was completely different."

    def _infer_thumbnail(self, request: str) -> Optional[str]:
        """Infer thumbnail subject from request."""
        keywords = ["rare", "secret", "hidden", "impossible", "insane", "betrayal", "fail"]
        for keyword in keywords:
            if keyword in request.lower():
                return keyword
        return None

    def _attach_knowledge_context(
        self,
        pkg_dir: str,
        idea: Dict,
        context: Dict,
        request: str,
    ):
        """Attach knowledge context to package for learning."""
        try:
            knowledge_data = {
                "generated_at": str(os.path.getmtime(pkg_dir)) if os.path.exists(pkg_dir) else None,
                "topic": context.get("topic", "unknown"),
                "request": request,
                "idea_hook": idea.get("hook", "N/A"),
                "tone": context.get("tone", "unknown"),
                "expertise_used": context.get("expertise", {}).get("key_elements", []),
                "trending_applied": list(context.get("trending", {}).get("trending_effects", [])) if context.get("trending") else [],
                "target_duration": idea.get("typical_duration") or idea["structure"].get("total_seconds"),
            }

            knowledge_file = Path(pkg_dir) / "knowledge_context.json"
            with open(knowledge_file, "w", encoding="utf-8") as f:
                json.dump(knowledge_data, f, indent=2)

        except Exception as e:
            print(f"Warning: Could not attach knowledge context: {e}")

    def learn_from_feedback(self, pkg_dir: str, engagement_score: float):
        """Update learning based on package engagement (0.0-1.0)."""
        try:
            # Load knowledge context from package
            knowledge_file = Path(pkg_dir) / "knowledge_context.json"
            if not knowledge_file.exists():
                return

            with open(knowledge_file, "r", encoding="utf-8") as f:
                context = json.load(f)

            # Update filter effectiveness based on feedback
            trending_applied = context.get("trending_applied", [])
            for effect in trending_applied:
                self.kb.update_filter_effectiveness(effect, engagement_score > 0.7)

            self.kb.save_all()

        except Exception as e:
            print(f"Warning: Could not learn from feedback: {e}")


def create_edit(
    topic: str,
    request: str,
    output_root: str = "output",
    target_seconds: float = 45.0,
    thumbnail_subject: Optional[str] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Convenience function: one-line API to create a video edit.

    Example:
        success, message, pkg_dir = create_edit("Minecraft", "make a betrayal edit")
        if success:
            print(f"Created: {pkg_dir}")
        else:
            print(f"Failed: {message}")
    """
    factory = AIVideoFactoryRequest(output_root)
    return factory.create_edit(topic, request, target_seconds, thumbnail_subject)
