import collections
from typing import List, Dict

# Example keywords for different categories, extend this based on actual content analysis
KEYWORD_CATEGORIES = {
    "gaming": ["gaming", "gameplay", "walkthrough", "lets play", "gamer", "video game"],
    "minecraft": ["minecraft", "mc", "survival", "build", "herobrine", "smp", "redstone", "modpack", "minecraft_server"],
    "tutorial": ["tutorial", "how to", "guide", "explanation", "lesson"],
    "vlog": ["vlog", "daily vlog", "personal", "life", "story"],
    "tech": ["tech", "technology", "review", "unboxing", "gadget"],
}

COMMON_GENERIC_TAGS = ["youtube", "video", "trending", "viral"]

def generate_youtube_tags(title: str, description: str, topic: str, max_tags: int = 15) -> List[str]:
    """Generates relevant YouTube tags based on video title, description, and main topic.

    Args:
        title (str): The title of the video.
        description (str): The description of the video.
        topic (str): The main topic/category of the video (e.g., "Minecraft", "Gaming").
        max_tags (int): The maximum number of tags to generate.

    Returns:
        List[str]: A list of YouTube tags.
    """
    generated_tags = collections.Counter()

    # 1. Tags from topic keywords
    topic_lower = topic.lower()
    for category, keywords in KEYWORD_CATEGORIES.items():
        if category in topic_lower or any(k in topic_lower for k in keywords):
            for keyword in keywords:
                generated_tags[keyword] += 2 # Higher weight for direct topic matches

    # 2. Tags from title and description
    text_to_analyze = (title + " " + description).lower()
    words = text_to_analyze.split()
    word_freq = collections.Counter(words)

    for word, freq in word_freq.items():
        if len(word) > 2 and word not in generated_tags: # Avoid very short words and duplicates
            generated_tags[word] += freq # Add words from title/description based on frequency

    # 3. Add generic tags
    for tag in COMMON_GENERIC_TAGS:
        generated_tags[tag] += 1 # Lower weight but always present

    # Sort tags by their "score" and pick the top ones
    sorted_tags = [tag for tag, _ in generated_tags.most_common()]

    # Ensure tags are unique and limit to max_tags
    unique_tags = []
    seen = set()
    for tag in sorted_tags:
        if tag not in seen:
            unique_tags.append(tag)
            seen.add(tag)
        if len(unique_tags) >= max_tags:
            break
            
    return unique_tags

if __name__ == "__main__":
    # Example Usage
    title = "My Epic Minecraft Betrayal Story - SMP Highlights"
    description = "Watch as I recount the most shocking betrayal on our Minecraft survival multiplayer server. Full of twists and turns!"
    topic = "Minecraft Gaming"

    tags = generate_youtube_tags(title, description, topic)
    print(f"Generated Tags: {tags}")

    title2 = "How to make a React App in 5 minutes"
    description2 = "A quick tutorial on setting up a new React project with Vite. Perfect for beginners!"
    topic2 = "Web Development Tutorial"
    tags2 = generate_youtube_tags(title2, description2, topic2, max_tags=10)
    print(f"Generated Tags (React): {tags2}")
