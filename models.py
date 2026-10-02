"""Static model registries used by the Zenbot web application.

The v3 runtime no longer treats finetuned Mumonbot checkpoints as the default
production path. Live inference is anchored on current general-purpose OpenAI
models, while prior finetunes remain catalogued for evaluation, archival, and
training-pipeline work.
"""

from __future__ import annotations


# Active live list. Keep the generic baseline available beside the finetunes.
MODELS_IN_USE = {
    "gpt-5.5": "gpt-5.5",
    "set03-bs2lr05e7": "ft:gpt-4.1-2025-04-14:aix-protodyne:set03-bs2lr05e7:DJg4yCUT",
    "set03a-bs5lr05e5": "ft:gpt-4.1-2025-04-14:aix-protodyne:set03a-bs5lr05e5:DKBGq1NX",
    "mmnk-bows-ble513": "ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble513:AVXPlofK",
    "mmnk-bows-ble5053": "ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble5053:AVXUbfHM",
    "bs8lr08e4-set00-02": "ft:gpt-4o-2024-08-06:dca:bs8lr08e4-set00-02:BIzpjOBE",
    "cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD220aUe",
}

# Legacy finetunes are retained for reference and offline evaluation.
LEGACY_FINETUNES = {
    "mmnk_ble824": "ft:gpt-4o-mini-2024-07-18:chatbot-tuners:mmnk-ble824:A7q3grXA",
    "mmnk_ble5053_cntxt": "ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble5053:ATHGiQEV",
    "mmnk_ble8053_cntxt": "ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble8053:AV7mu6NG",
    "zbset01-bs3-lr15-ne2": "ft:gpt-4o-2024-08-06:dca:set01-bs3-lr15-ne2:AyP4ENFm",
    "zbset01-bs3-lr075-ne2": "ft:gpt-4o-2024-08-06:dca:zbset01-bs3-lr075-ne2:AyMDelJ3",
    "zbset01-bs5-lr1-ne3": "ft:gpt-4o-2024-08-06:dca:set01-bs5-lr1-ne3-av7ncsif-s20:B3ntgBgb",
    "base-bs8lr05e2": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2:B9PxHhZT",
    "base-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr08e3:B9NVrQ17",
    "base-bs6lr01e2": "ft:gpt-4o-2024-08-06:dca:base-bs6lr01e2:B9KhhY0V",
    "base-bs8lr08e3-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1fEeyM",
    "base-bs8lr05e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2-cntxt-bs8lr08e3:BD1eYc2n",
    "base-bs6lr01e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1p2sLf",
    "set00-02mix-bs10lr05e2": "ft:gpt-4o-2024-08-06:dca:bd1eyc2n-set00-02mix-bs10lr05e2:BJ02Ragt",
    "set00-02mix-bs6lr06e3": "ft:gpt-4o-2024-08-06:dca:b9khhy0v-set00-02mix-bs6lr06e3:BJ09MKSB",
}

MODEL_LOSSES = {
    "gpt-5.5": 0.0,
    "gpt-5.4-mini": 0.0,
    "gpt-5.4": 0.0,
    "gpt-4.1": 0.0,
    "xcset01-bs4-lr04-ne4": 0.3,
    "xcset01-bs4-lr04-ne4-s54": 0.5,
    "xcset01-bs2-lr04-ne4": 0.27,
    "xcset01-bs2-lr04-ne4-s108": 0.33,
    "xcset01-bs3-lr3-ne3": 1.86,
    "xcset01-bs3-lr1-ne3": 1.99,
    "xcset01-bs3-lr1-ne3-s72": 0.29,
    "xcset01-bs3-lr033-ne3": 2.09,
    "zbset01-bs5-lr1-ne3": 1.77,
    "zbset01-bs3-lr15-ne2": 0.296,
    "zbset01-bs3-lr075-ne2": 0.38,
    "mmnk_ble824": 1.32,
    "mmnk_ble5053_cntxt": 0.515,
    "mmnk_ble8053_cntxt": 0.747,
    "mmnk-bows-ble513": 0.334,
    "mmnk-bows-ble5053": 0.49,
    "base-bs8lr08e3": 1.6,
    "base-bs8lr05e2": 3.03,
    "base-bs6lr01e2": 3.01,
    "base-bs8lr08e3-cntxt-bs8lr08e3": 0.5968,
    "base-bs8lr05e2-cntxt-bs8lr08e3": 0.6002,
    "base-bs6lr01e2-cntxt-bs8lr08e3": 0.5997,
    "cntxt-bs8lr08e3": 0.5759,
    "bs8lr08e4-set00-02": 0.555,
    "set00-02mix-bs10lr05e2": 0.3922,
    "set00-02mix-bs6lr06e3": 0.5753,
    "set03-bs2lr05e7": 0.858,
    "set03a-bs5lr05e5": 1.874,
}
