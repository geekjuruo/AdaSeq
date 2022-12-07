# Copyright (c) Alibaba, Inc. and its affiliates.
from typing import Any, Dict, List, Optional, Tuple, Union

from modelscope.preprocessors.base import Preprocessor
from modelscope.preprocessors.builder import PREPROCESSORS

from adaseq.data.tokenizer import build_tokenizer
from adaseq.metainfo import Preprocessors
from adaseq.utils.data_utils import gen_label2id


@PREPROCESSORS.register_module(module_name=Preprocessors.nlp_preprocessor)
class NLPPreprocessor(Preprocessor):
    """
    Some common pre-process operations for NLP tasks.

    Args:
        model_dir (str): pre-trained model name or path.
        tokenizer_kwargs (Optional[Dict[str, Any]]): some arguments to init tokenizer
            from huggingface, modelscope or ...
        max_length (int): we will discard tokens that exceed the `max_length`.
            So please take care of this argument.
        return_offsets (bool): if `True`, compute sub-token offset mapping for the
            original sequence reconstruction in the `TransformerEncoder`.
        add_special_tokens (bool): add special tokens of pre-trained models to the
            input, it is only effective when `return_offsets==False`.
        label_to_id (Dict[str, int]): a dict maps label to index,
            such as `{'O': 0, 'B-LOC': 1, 'I-LOC': 2}`.
            It is presetted for future updates.
        return_original_view (bool): if `True`, return token_ids and other tensors
            that without padded context, only used in retrieval-augmented models.
    """

    def __init__(
        self,
        model_dir: str,
        tokenizer_kwargs: Optional[Dict[str, Any]] = None,
        max_length: int = 512,
        return_offsets: bool = False,
        add_special_tokens: bool = True,
        label_to_id: Optional[Dict[str, int]] = None,
        return_original_view: bool = False,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.max_length = max_length
        self.add_special_tokens = add_special_tokens
        self.return_offsets = return_offsets
        self.label_to_id = label_to_id
        self.return_original_view = return_original_view
        self.tokenizer = build_tokenizer(model_dir, **(tokenizer_kwargs or {}))

    def __call__(self, data: Union[str, List, Dict]) -> Dict[str, Any]:
        """
        Encode one instance, it could be a text str, a list of tokens for a dict.

        Returns:
            Dict[str, Any]: `{'tokens': tokenized and encoded tensors, 'meta': data input}`
        """
        if isinstance(data, str):
            data = {'text': data}
        if isinstance(data, List):
            data = {'tokens': data}

        output_dict = {'meta': data}

        if 'tokens' in data:
            output_dict['tokens'] = self.encode_tokens(data['tokens'])
        elif 'text' in data:
            output_dict['tokens'] = self.encode_text(data['text'])
        else:
            raise ValueError('Data sample must have "text" or "tokens" field!')

        if self.return_original_view:
            # return token_ids and other tensors that without padded context,
            # only used in retrieval-augmented models.
            output_dict['origin_tokens'] = self.encode_tokens_origin_view(data)

        return output_dict

    def encode_text(self, text: str) -> Dict[str, Any]:
        """encode `text` to ids"""
        assert self.return_offsets is False
        output = self.tokenizer.encode_plus(
            text,
            add_special_tokens=self.add_special_tokens,
            return_tensors=None,
            return_offsets_mapping=False,
        )
        output['has_special_tokens'] = self.add_special_tokens
        return output

    def encode_tokens(self, tokens: List[str]) -> Dict[str, Any]:
        """conver tokens to ids, add some mask."""
        input_ids = []
        # the corresponding inclusive sub-token span of tokens
        offsets: List[Optional[Tuple[int, int]]] = []

        if self.add_special_tokens:
            input_ids.append(self.tokenizer.cls_token_id)
            offsets.append((0, 0))

        # if `add_special_tokens`, the max_length should minus 1 for the appending `[SEP]`
        max_length = self.max_length - int(self.add_special_tokens)

        for token_string in tokens:
            wordpieces = self.tokenizer.encode_plus(
                token_string,
                add_special_tokens=False,
                return_tensors=None,
                return_offsets_mapping=False,
                return_attention_mask=False,
            )
            wp_ids = wordpieces['input_ids']

            # For tokens that don't correspond to any word pieces, we set it to [UNK].
            if len(wp_ids) == 0:
                wp_ids = [self.tokenizer.unk_token_id]

            offsets.append((len(input_ids), len(input_ids) + len(wp_ids) - 1))
            input_ids.extend(wp_ids)

            if len(input_ids) > max_length:
                # discard sub-tokens that exceed the `max_length`
                input_ids = input_ids[:max_length]
                offsets[-1] = (offsets[-1][0], len(input_ids) - 1)
                break

        if self.add_special_tokens:
            offsets.append((len(input_ids), len(input_ids)))
            input_ids.append(self.tokenizer.sep_token_id)

        output = {
            'input_ids': input_ids,
            'attention_mask': [True] * len(input_ids),
            'has_special_tokens': self.add_special_tokens,
        }
        if self.return_offsets:
            output['mask'] = [True] * len(offsets)
            output['offsets'] = offsets
        return output

    def map_label_to_id(
        self, labels: List[str] = None, label2id: Dict[str, int] = None
    ) -> Dict[str, int]:
        """conver labels to ids"""
        if label2id is not None:
            return label2id
        elif labels is not None:
            return self._label2id(labels)
        else:
            raise ValueError('labels or label2id is needed.')

    def _label2id(self, labels: List[str]) -> Dict[str, int]:
        return gen_label2id(labels)

    def encode_tokens_origin_view(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """encode tokens when using retrieval-augmented multi-view model."""
        tokens = data['tokens']
        mask = data.get('mask', [True] * len(tokens))
        # remove the padded context
        origin_length = sum(mask)
        origin_tokens = tokens[:origin_length]
        return self.encode_tokens(origin_tokens)
