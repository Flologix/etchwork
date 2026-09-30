Design the PCB for a relay module that lets a 3.3 V microcontroller switch
a 12 V relay.

- 12 V power enters on a 2-way screw terminal: terminal 1 is +12 V,
  terminal 2 is ground.
- A 3-pin 2.54 mm header connects to the microcontroller board: pin 1 is
  ground, pin 2 is the control signal (3.3 V logic; high turns the relay
  on), pin 3 is the microcontroller's 3.3 V supply.
- The relay has a 12 V coil and is switched by an N-channel MOSFET.
- Leave the relay's switch contacts unconnected; a later revision adds
  their terminal.

Choose component values from the parts' datasheets. Deliver a fab-ready board.
