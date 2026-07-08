from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

VIDEO_QUALITIES = [

    "144",

    "240",

    "360",

    "480",

    "720",

]

AUDIO_FORMATS = [

    "mp3",

    "m4a",

    "voice",

]


def quality_keyboard():

    keyboard = []

    for quality in VIDEO_QUALITIES:

        keyboard.append(

            [

                InlineKeyboardButton(

                    text=quality,

                    callback_data=f"quality:{quality}",

                )

            ]

        )

    keyboard.append(

        [

            InlineKeyboardButton(

                text="🎵 MP3",

                callback_data="quality:mp3",

            )

        ]

    )

    keyboard.append(

        [

            InlineKeyboardButton(

                text="🎵 M4A",

                callback_data="quality:m4a",

            )

        ]

    )

    keyboard.append(

        [

            InlineKeyboardButton(

                text="🎤 Voice",

                callback_data="quality:voice",

            )

        ]

    )

    return InlineKeyboardMarkup(

        inline_keyboard=keyboard,

    )
