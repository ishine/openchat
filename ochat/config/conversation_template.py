from typing import Optional, Callable, Iterable, List, Dict
import re

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(..., alias="from")
    value: str

    weight: Optional[float] = None


class Conversation(BaseModel):
    items: List[Message]

    condition: Optional[str] = None
    system: str = ""


class ConversationTemplate(BaseModel):
    tokenizer: Callable

    # Prompt
    role_prefix: Callable
    eot: str

    inference_condition: Optional[str] = None

    # Private
    bos_tokens_: List[int]
    eot_tokens_: List[int]

    def __init__(self, **data):
        tokenizer = data["tokenizer"]
        eot = data["eot"]
        bos_tokens_ = tokenizer("").input_ids
        eot_tokens_ = tokenizer(eot, add_special_tokens=False).input_ids

        super().__init__(**data, bos_tokens_=bos_tokens_, eot_tokens_=eot_tokens_)

    def safe_tokenize(self, strings: Iterable[str]) -> List[List[int]]:
        return self.tokenizer(strings, split_special_tokens=True, return_attention_mask=False, add_special_tokens=False).input_ids

    def tokenize_conversations(self, conversations: Iterable[Conversation], inference: bool = False):
        # Pre-tokenize all conversations
        default_condition = self.inference_condition if inference else None

        sys_mappings = set()
        role_mappings = set()
        all_text = []
        for conv in conversations:
            sys_mappings.add(conv.system)
            for msg in conv.items:
                role_mappings.add((msg.role, conv.condition or default_condition))
                all_text.append(msg.value)

        sys_mappings = list(sys_mappings)
        role_mappings = list(role_mappings)

        # Tokenize
        sys_mappings = dict(zip(sys_mappings, self.safe_tokenize(sys_mappings)))
        role_mappings = dict(zip(role_mappings, self.safe_tokenize([self.role_prefix(*args) for args in role_mappings])))
        all_text = self.safe_tokenize(all_text)

        # Convert
        result_tokens = []
        result_weights = []
        all_text_idx = 0
        for conv in conversations:
            tokens = []
            weights = []

            # bos tokens
            tokens.extend(self.bos_tokens_)
            weights.extend([0.] * len(self.bos_tokens_))

            # System
            if conv.system:
                system = sys_mappings[conv.system]
                tokens.extend(system)
                weights.extend([0.] * len(system))

                tokens.extend(self.eot_tokens_)
                weights.extend([0.] * len(self.eot_tokens_))

            # Messages
            last_idx = len(conv.items) - 1
            for idx, msg in enumerate(conv.items):
                # Prefix
                role = role_mappings[(msg.role, conv.condition or default_condition)]
                tokens.extend(role)
                weights.extend([0.] * len(role))

                # Message
                text = all_text[all_text_idx]
                all_text_idx += 1

                if not inference:
                    assert msg.weight is not None

                tokens.extend(text)
                weights.extend([msg.weight] * len(text))

                if not (inference and idx == last_idx):  # Do not add EOT on last turn during inference
                    tokens.extend(self.eot_tokens_)
                    weights.extend([msg.weight] * len(self.eot_tokens_))

            # Append result
            result_tokens.append(tokens)
            result_weights.append(weights)

        # Sanity check
        assert all_text_idx == len(all_text)

        return result_tokens, result_weights