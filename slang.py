import re

# Comprehensive Wolvesville Slang & Abbreviations Map
SLANG_MAP = {
    # Solo Killers
    r"\bsk\b|\bkiller\b": "Serial Killer",
    r"\barso\b": "Arsonist",
    r"\bcanni\b|\bcb\b": "Cannibal",
    r"\bcorr\b": "Corruptor",
    r"\bbomb\b|\bbb\b": "Bomber",
    r"\billu\b": "Illusionist",
    r"\balch\b|\balche\b": "Alchemist",
    r"\bed\b|\bevil det\b": "Evil Detective",
    r"\bsl\b|\bsect\b": "Sect Leader",
    r"\bzb\b|\bzomb\b": "Zombie",
    r"\bacc\b|\bacco\b": "Accomplice",
    r"\bbd\b": "Bandit",

    # Village Roles
    r"\bdoc\b": "Doctor",
    r"\bbg\b|\bguard\b": "Bodyguard",
    r"\btg\b": "Tough Guy",
    r"\bvigi\b|\bvigil\b": "Vigilante",
    r"\brl\b": "Red Lady",
    r"\bgl\b": "Ghost Lady",
    r"\blm\b": "Loudmouth",
    r"\bbh\b": "Beast Hunter",
    r"\bdet\b": "Detective",
    r"\bmed\b": "Medium",
    r"\baura\b|\bas\b": "Aura Seer",
    r"\bfc\b": "Flower Child",
    r"\bnm\b|\bnwm\b": "Night Watchman",
    r"\bflag\b": "Flagger",
    r"\btrap\b": "Trapper",
    r"\bpaci\b": "Pacifist",
    r"\bana\b": "Analyst",
    r"\bgranny\b": "Grumpy Grandma",

    # Werewolf Roles
    r"\bregw\b": "Regular Werewolf",
    r"\bjww\b|\bjrww\b": "Junior Werewolf",
    r"\baww\b|\balpha\b": "Alpha Werewolf",
    r"\bkww\b|\bkitten\b": "Kitten Wolf",
    r"\bsww\b|\bshadow\b|\bstorm\b": "Shadow/Storm Wolf",
    r"\bnww\b|\bnightmare\b": "Nightmare Werewolf",
    r"\bbers\b|\bwwb\b": "Werewolf Berserk",
    r"\bwws\b|\bsummon\b|\bshaman\b": "Wolf Summoner/Shaman",

    # Other Common Terms
    r"\bhh\b": "Headhunter",
    r"\bww\b": "Werewolf",
    r"\bvill\b|\bforky\b": "Villager",
    r"\bunk\b": "Unknown",
    r"\brww\b": "Random Werewolf",
    r"\brsk\b|\brk\b": "Random Solo Killer",
    r"\brv\b": "Random Voting",
    r"\bsus\b": "Suspicious",
    r"\bprot\b|\bcv\b": "Protect",
    r"\bcheck\b": "Investigate",
    r"\bshoot\b": "Shoot",
    r"\brev\b": "Revive",
    r"\btag\b": "Tag/Avenge",
    r"\bgg\b": "Good Game",
    r"\bgl\b": "Good Luck",
    r"\bgj\b": "Good Job",
    
    # Additional
    r"\bes\b": "Evil Santa",
    r"\badmin\b|\badmi\b": "Admirer",
    r"\bsb\b": "Soulbinder",
    r"\bferry\b": "Ferryman"
}

class SlangExpander:
    def __init__(self):
        # Pre-compile regex patterns for performance
        self.patterns = {re.compile(k, re.IGNORECASE): v for k, v in SLANG_MAP.items()}

    def expand(self, text):
        """
        Translates abbreviations in the given text to their full terms.
        """
        if not isinstance(text, str):
            return ""
        
        expanded = text
        for pattern, translation in self.patterns.items():
            expanded = pattern.sub(translation, expanded)
        return expanded

# Singleton instance for easy import
expander = SlangExpander()

def expand_slang(text):
    return expander.expand(text)
