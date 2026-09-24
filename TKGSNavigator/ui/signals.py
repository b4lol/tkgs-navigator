"""Bridge the two Enigma2 signal styles (Python callback lists and eSignal)."""


def bind_signal(owner, name, callback):
    signal = getattr(owner, name)
    if hasattr(signal, "connect"):
        return signal.connect(callback)
    callbacks = signal.get() if hasattr(signal, "get") else signal
    callbacks.append(callback)
    return (callbacks, callback)


def unbind_signal(binding):
    if isinstance(binding, tuple):
        callbacks, callback = binding
        if callback in callbacks:
            callbacks.remove(callback)
    elif hasattr(binding, "disconnect"):
        binding.disconnect()
