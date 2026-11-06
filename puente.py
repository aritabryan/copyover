"""Old name of bridge.py, kept so `python puente.py` and shortcuts from earlier versions still start CopyOver.

It only calls bridge.main(): the state lives in the bridge module, never in a copy of it.
"""
import bridge

if __name__ == "__main__":
    bridge.main()
