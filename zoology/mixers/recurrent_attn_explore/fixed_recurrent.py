import torch 
from torch import nn
import torch.nn.functional as F
from einops import rearrange
import math


class SelfAttention(nn.Module):
    def __init__(self, attention_dropout=0.0):
        super().__init__()
        self.dropout_p = attention_dropout

    def forward(self, qkv):
        """Implements the multihead softmax attention.
        Arguments
        ---------
            qkv: The tensor containing the query, key, and value. (B, S, 3, H, D)
            causal: if passed, will override self.causal
        """
        seqlen = qkv.shape[1]
        q, k, v = qkv.unbind(dim=2)
        softmax_scale = 1.0 / math.sqrt(q.shape[-1])
        scores = torch.einsum("bthd,bshd->bhts", q, k * softmax_scale)
        causal_mask = torch.triu(
            torch.full((seqlen, seqlen), -10000.0, device=scores.device), 1
        )
        scores = scores + causal_mask.to(dtype=scores.dtype)
        attention = torch.softmax(scores, dim=-1, dtype=v.dtype)
        attention_drop = F.dropout(attention, self.dropout_p if self.training else 0.0)
        output = torch.einsum("bhts,bshd->bthd", attention_drop, v)
        return output


class MHR(nn.Module):
    """Multi-head attention with recurrencess
    """

    def __init__(
        self,
        d_model: int,
        num_heads: int=1,
        bias: bool=True,
        dropout: float=0.0,
        layer_idx: int=None,
        repetitions: int=0, # how many local repititions to do
        rep_dim: str = 'q' # which dimension to perform the recurrence over

    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.layer_idx = layer_idx
        self.num_heads = num_heads
        assert (
            self.d_model % num_heads == 0
        ), "self.kdim must be divisible by num_heads"
        self.head_dim = self.d_model // num_heads
        self.Wqkv = nn.Linear(
            d_model, 3 * d_model, bias=bias
        )
        self.inner_attn = SelfAttention(attention_dropout=dropout)
        self.out_proj = nn.Linear(d_model, d_model)
        self.repititions = repetitions # repeated attention op
        self.rep_dim = rep_dim # which dimension to have the repition over

    def forward(self, x: torch.Tensor):
        """
        Forward pass of the model. The function computes attention with possible recurrences, where
        the query, key, or value can be replaced by the result from a previous attention calculation,
        summed over each recurrence step.
        """
        qkv = self.Wqkv(x)
        qkv = rearrange(
            qkv, "... (three h d) -> ... three h d", three=3, d=self.head_dim
        )
        context = self.inner_attn(qkv)

        if self.repititions > 1:
            q, k, v = qkv.unbind(dim=2)
            for _ in range(self.repititions - 1):
                if self.rep_dim == 'q':
                    qkv = torch.stack([context, k, v], dim=2)
                elif self.rep_dim == 'k':
                    qkv = torch.stack([q, context, v], dim=2)

                context = self.inner_attn(qkv)

        out = self.out_proj(rearrange(context, "... h d -> ... (h d)"))
        return out
    
    def state_size(self, batch_size: int=1, sequence_length: int=2048):
        return 2 * self.d_model * sequence_length