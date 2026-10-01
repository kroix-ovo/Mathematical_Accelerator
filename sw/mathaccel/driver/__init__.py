"""Driver components, ordered from byte handling to useful device commands.

framing encodes bytes, protocol describes packets, and transport moves them.
Device joins those pieces; DeviceModel supplies a software reference backend.
"""
