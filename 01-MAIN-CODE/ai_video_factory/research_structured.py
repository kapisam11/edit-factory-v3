"""AI Video Factory — Structured Research Module.

Replaces the blob-of-text approach with structured, actionable data.
"""
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class ResearchResult:
    title: str
    query: str
    key_facts: List[str] = None
    quotes: List[str] = None
    hook_angles: List[str] = None
    controversies: List[str] = None
    related_topics: List[str] = None
    image_urls: List[str] = None
    video_urls: List[str] = None
    source_urls: List[str] = None
    summary_text: str = ""
    raw_data: Dict = None
    
    def __post_init__(self):
        if self.key_facts is None:
            self.key_facts = []
        if self.quotes is None:
            self.quotes = []
        if self.hook_angles is None:
            self.hook_angles = []
        if self.controversies is None:
            self.controversies = []
        if self.related_topics is None:
            self.related_topics = []
        if self.image_urls is None:
            self.image_urls = []
        if self.video_urls is None:
            self.video_urls = []
        if self.source_urls is None:
            self.source_urls = []
        if self.raw_data is None:
            self.raw_data = {}
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def from_raw_scrape(cls, query: str, raw: Dict) -> "ResearchResult":
        """Convert raw scraped data into structured ResearchResult."""
        summary = raw.get("summary_text", "")
        
        # Extract key facts (sentences that look like facts)
        sentences = re.split(r'[.!?]+', summary)
        key_facts = [
            s.strip() for s in sentences
            if len(s.strip()) > 20 and len(s.strip()) < 200
            and not s.strip().startswith(("However", "But", "Although", "Yet"))
        ][:10]
        
        # Extract potential quotes (text in quotes)
        quotes = re.findall(r'"([^\"]{10,200})"', summary)
        
        # Generate hook angles
        hook_angles = _generate_hook_angles(query, key_facts)
        
        # Extract related topics (capitalized phrases)
        related = list(set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', summary)))[:10]
        
        return cls(
            title=raw.get("title", query),
            query=query,
            key_facts=key_facts,
            quotes=quotes,
            hook_angles=hook_angles,
            controversies=[],  # Would need sentiment analysis
            related_topics=related,
            image_urls=raw.get("images", []),
            video_urls=raw.get("videos", []),
            source_urls=raw.get("sources", []),
            summary_text=summary,
            raw_data=raw,
        )
    
    @classmethod
    def from_llm_response(cls, query: str, llm_text: str) -> "ResearchResult":
        """Parse structured research from an LLM response."""
        # Try to extract JSON from LLM response
        try:
            json_match = re.search(r'\{.*\}', llm_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return cls(
                    title=data.get("title", query),
                    query=query,
                    key_facts=data.get("key_facts", []),
                    quotes=data.get("quotes", []),
                    hook_angles=data.get("hook_angles", _generate_hook_angles(query, data.get("key_facts", []))),
                    controversies=data.get("controversies", []),
                    related_topics=data.get("related_topics", []),
                    summary_text=data.get("summary", llm_text),
                )
        except json.JSONDecodeError:
            pass
        
        # Fallback: treat entire text as summary
        return cls(
            title=query,
            query=query,
            summary_text=llm_text,
            key_facts=[s.strip() for s in re.split(r'[.!?]+', llm_text) if 20 < len(s.strip()) < 200][:10],
        )


def _generate_hook_angles(topic: str, facts: List[str]) -> List[str]:
    """Generate potential video hook angles from facts."""
    hooks = []
    templates = [
        "Nobody expected what happened with {topic}",
        "The real reason {topic} went viral",
        "What they don't tell you about {topic}",
        "I was shocked when I learned this about {topic}",
        "The hidden truth behind {topic}",
        "This changes everything about {topic}",
        "Why {topic} is not what you think",
    ]
    for template in templates:
        hooks.append(template.format(topic=topic))
    
    # Add fact-based hooks
    for fact in facts[:3]:
        if "betrayal" in fact.lower() or "backstab" in fact.lower():
            hooks.append(f"The betrayal nobody saw coming: {fact[:60]}...")
        if "rare" in fact.lower() or "impossible" in fact.lower():
            hooks.append(f"Impossible odds: {fact[:60]}...")
        if "secret" in fact.lower() or "hidden" in fact.lower():
            hooks.append(f"The secret they tried to hide: {fact[:60]}...")
    
    return hooks[:8]
"""AI Video Factory — Structured Research Module.

Replaces the blob-of-text approach with structured, actionable data.
"""
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class ResearchResult:
    title: str
    query: str
    key_facts: List[str] = None
    quotes: List[str] = None
    hook_angles: List[str] = None
    controversies: List[str] = None
    related_topics: List[str] = None
    image_urls: List[str] = None
    video_urls: List[str] = None
    source_urls: List[str] = None
    summary_text: str = ""
    raw_data: Dict = None
    
    def __post_init__(self):
        if self.key_facts is None:
            self.key_facts = []
        if self.quotes is None:
            self.quotes = []
        if self.hook_angles is None:
            self.hook_angles = []
        if self.controversies is None:
            self.controversies = []
        if self.related_topics is None:
            self.related_topics = []
        if self.image_urls is None:
            self.image_urls = []
        if self.video_urls is None:
            self.video_urls = []
        if self.source_urls is None:
            self.source_urls = []
        if self.raw_data is None:
            self.raw_data = {}
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def from_raw_scrape(cls, query: str, raw: Dict) -> "ResearchResult":
        summary = raw.get("summary_text", "")
        sentences = re.split(r'[.!?]+', summary)
        key_facts = [
            s.strip() for s in sentences
            if len(s.strip()) > 20 and len(s.strip()) < 200
            and not s.strip().startswith(("However", "But", "Although", "Yet"))
        ][:10]
        
        quotes = re.findall(r'"([^"]{10,200})"', summary)
        hook_angles = _generate_hook_angles(query, key_facts)
        related = list(set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', summary)))[:10]
        
        return cls(
            title=raw.get("title", query),
            query=query,
            key_facts=key_facts,
            quotes=quotes,
            hook_angles=hook_angles,
            controversies=[],
            related_topics=related,
            image_urls=raw.get("images", []),
            video_urls=raw.get("videos", []),
            source_urls=raw.get("sources", []),
            summary_text=summary,
            raw_data=raw,
        )
    
    @classmethod
    def from_llm_response(cls, query: str, llm_text: str) -> "ResearchResult":
        try:
            json_match = re.search(r'\{.*\}', llm_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return cls(
                    title=data.get("title", query),
                    query=query,
                    key_facts=data.get("key_facts", []),
                    quotes=data.get("quotes", []),
                    hook_angles=data.get("hook_angles", _generate_hook_angles(query, data.get("key_facts", []))),
                    controversies=data.get("controversies", []),
                    related_topics=data.get("related_topics", []),
                    summary_text=data.get("summary", llm_text),
                )
        except json.JSONDecodeError:
            pass
        
        return cls(
            title=query,
            query=query,
            summary_text=llm_text,
            key_facts=[s.strip() for s in re.split(r'[.!?]+', llm_text) if 20 < len(s.strip()) < 200][:10],
        )


def _generate_hook_angles(topic: str, facts: List[str]) -> List[str]:
    hooks = []
    templates = [
        "Nobody expected what happened with {topic}",
        "The real reason {topic} went viral",
        "What they don't tell you about {topic}",
        "I was shocked when I learned this about {topic}",
        "The hidden truth behind {topic}",
        "This changes everything about {topic}",
        "Why {topic} is not what you think",
    ]
    for template in templates:
        hooks.append(template.format(topic=topic))
    
    for fact in facts[:3]:
        if "betrayal" in fact.lower() or "backstab" in fact.lower():
            hooks.append(f"The betrayal nobody saw coming: {fact[:60]}...")
        if "rare" in fact.lower() or "impossible" in fact.lower():
            hooks.append(f"Impossible odds: {fact[:60]}...")
        if "secret" in fact.lower() or "hidden" in fact.lower():
            hooks.append(f"The secret they tried to hide: {fact[:60]}...")
    
    return hooks[:8]
"""AI Video Factory — Structured Research Module.

Replaces the blob-of-text approach with structured, actionable data.
"""
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class ResearchResult:
    title: str
    query: str
    key_facts: List[str] = None
    quotes: List[str] = None
    hook_angles: List[str] = None
    controversies: List[str] = None
    related_topics: List[str] = None
    image_urls: List[str] = None
    video_urls: List[str] = None
    source_urls: List[str] = None
    summary_text: str = ""
    raw_data: Dict = None
    
    def __post_init__(self):
        if self.key_facts is None:
            self.key_facts = []
        if self.quotes is None:
            self.quotes = []
        if self.hook_angles is None:
            self.hook_angles = []
        if self.controversies is None:
            self.controversies = []
        if self.related_topics is None:
            self.related_topics = []
        if self.image_urls is None:
            self.image_urls = []
        if self.video_urls is None:
            self.video_urls = []
        if self.source_urls is None:
            self.source_urls = []
        if self.raw_data is None:
            self.raw_data = {}
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def from_raw_scrape(cls, query: str, raw: Dict) -> "ResearchResult":
        """Convert raw scraped data into structured ResearchResult."""
        summary = raw.get("summary_text", "")
        
        # Extract key facts (sentences that look like facts)
        sentences = re.split(r'[.!?]+', summary)
        key_facts = [
            s.strip() for s in sentences
            if len(s.strip()) > 20 and len(s.strip()) < 200
            and not s.strip().startswith(("However", "But", "Although", "Yet"))
        ][:10]
        
        # Extract potential quotes (text in quotes)
        quotes = re.findall(r'"([^\"]{10,200})"', summary)
        
        # Generate hook angles
        hook_angles = _generate_hook_angles(query, key_facts)
        
        # Extract related topics (capitalized phrases)
        related = list(set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', summary)))[:10]
        
        return cls(
            title=raw.get("title", query),
            query=query,
            key_facts=key_facts,
            quotes=quotes,
            hook_angles=hook_angles,
            controversies=[],  # Would need sentiment analysis
            related_topics=related,
            image_urls=raw.get("images", []),
            video_urls=raw.get("videos", []),
            source_urls=raw.get("sources", []),
            summary_text=summary,
            raw_data=raw,
        )
    
    @classmethod
    def from_llm_response(cls, query: str, llm_text: str) -> "ResearchResult":
        """Parse structured research from an LLM response."""
        # Try to extract JSON from LLM response
        try:
            json_match = re.search(r'\{.*\}', llm_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return cls(
                    title=data.get("title", query),
                    query=query,
                    key_facts=data.get("key_facts", []),
                    quotes=data.get("quotes", []),
                    hook_angles=data.get("hook_angles", _generate_hook_angles(query, data.get("key_facts", []))),
                    controversies=data.get("controversies", []),
                    related_topics=data.get("related_topics", []),
                    summary_text=data.get("summary", llm_text),
                )
        except json.JSONDecodeError:
            pass
        
        # Fallback: treat entire text as summary
        return cls(
            title=query,
            query=query,
            summary_text=llm_text,
            key_facts=[s.strip() for s in re.split(r'[.!?]+', llm_text) if 20 < len(s.strip()) < 200][:10],
        )


def _generate_hook_angles(topic: str, facts: List[str]) -> List[str]:
    """Generate potential video hook angles from facts."""
    hooks = []
    templates = [
        "Nobody expected what happened with {topic}",
        "The real reason {topic} went viral",
        "What they don't tell you about {topic}",
        "I was shocked when I learned this about {topic}",
        "The hidden truth behind {topic}",
        "This changes everything about {topic}",
        "Why {topic} is not what you think",
    ]
    for template in templates:
        hooks.append(template.format(topic=topic))
    
    # Add fact-based hooks
    for fact in facts[:3]:
        if "betrayal" in fact.lower() or "backstab" in fact.lower():
            hooks.append(f"The betrayal nobody saw coming: {fact[:60]}...")
        if "rare" in fact.lower() or "impossible" in fact.lower():
            hooks.append(f"Impossible odds: {fact[:60]}...")
        if "secret" in fact.lower() or "hidden" in fact.lower():
            hooks.append(f"The secret they tried to hide: {fact[:60]}...")
    
    return hooks[:8]
