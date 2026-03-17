"""Static model registries used by the Zenbot web application.

This module is intentionally data-only. It keeps finetuned model identifiers
and their associated training-loss metadata out of request-handling code so the
runtime configuration layer can select and expose the currently supported model
set without importing deployment or API logic.
"""

MODELS_IN_USE = {
    "set03-bs2lr05e7": "ft:gpt-4.1-2025-04-14:aix-protodyne:set03-bs2lr05e7:DJg4yCUT",
    "set03a-bs5lr05e5": "ft:gpt-4.1-2025-04-14:aix-protodyne:set03a-bs5lr05e5:DKBGq1NX",
    }

ZB_MODELS = {
    "mmnk_ble824": "ft:gpt-4o-mini-2024-07-18:chatbot-tuners:mmnk-ble824:A7q3grXA",
    "mmnk_ble5053_cntxt": "ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble5053:ATHGiQEV",
    "mmnk_ble8053_cntxt": "ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble8053:AV7mu6NG",
    "mmnk-bows-ble513": "ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble513:AVXPlofK",
    "mmnk-bows-ble5053": "ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble5053:AVXUbfHM",
    "zbset01-bs3-lr15-ne2": "ft:gpt-4o-2024-08-06:dca:set01-bs3-lr15-ne2:AyP4ENFm",
    "zbset01-bs3-lr075-ne2": "ft:gpt-4o-2024-08-06:dca:zbset01-bs3-lr075-ne2:AyMDelJ3",
    "zbset01-bs5-lr1-ne3": "ft:gpt-4o-2024-08-06:dca:set01-bs5-lr1-ne3-av7ncsif-s20:B3ntgBgb",
    "base-bs8lr05e2": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2:B9PxHhZT",
    "base-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr08e3:B9NVrQ17",
    "base-bs6lr01e2": "ft:gpt-4o-2024-08-06:dca:base-bs6lr01e2:B9KhhY0V",
    "base-bs8lr08e3-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1fEeyM",
    "base-bs8lr05e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2-cntxt-bs8lr08e3:BD1eYc2n",
    "base-bs6lr01e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1p2sLf",
    "cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD220aUe",
    "bs8lr08e4-set00-02": "ft:gpt-4o-2024-08-06:dca:bs8lr08e4-set00-02:BIzpjOBE",
    "set00-02mix-bs10lr05e2": "ft:gpt-4o-2024-08-06:dca:bd1eyc2n-set00-02mix-bs10lr05e2:BJ02Ragt",
    "set00-02mix-bs6lr06e3": "ft:gpt-4o-2024-08-06:dca:b9khhy0v-set00-02mix-bs6lr06e3:BJ09MKSB",
    "set03-bs2lr05e7": "ft:gpt-4.1-2025-04-14:aix-protodyne:set03-bs2lr05e7:DJg4yCUT"
}

XC_MODELS = {
    "xcset01-bs3-lr3-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr3-ne3:Ay3Xz411",
    "xcset01-bs3-lr033-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr033-ne3:AxzCKK12",
    "xcset01-bs4-lr04-ne4": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs4-lr04-ne4:Ay3MdXVg",
    "xcset01-bs4-lr04-ne4-s54": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs4-lr04-ne4:Ay3McO8O:ckpt-step-54",
    "xcset01-bs2-lr04-ne4": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs2-lr04-ne4:Ay3TpHKf",
    "xcset01-bs2-lr04-ne4-s108": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs2-lr04-ne4:Ay3TpHKf:ckpt-step-108",
    "xcset01-bs3-lr1-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr1-ne3:Ay3OQylM",
    "xcset01-bs3-lr1-ne3-s72": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr1-ne3:Ay3OQS40:ckpt-step-72",
}

# Training loss values are used for metadata, selection context, and admin views.
MODEL_LOSSES = {
    "gpt-4": 0.0,
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
}
