import streamlit as st

st.title('hola mundo loco')

# Solicitar un entero con un paso de 1
edad = st.number_input('Introduce tu edad:', min_value=0, max_value=120, value=25, step=1)

st.write(f'La edad ingresada es: {edad}')
st.write(f'Tipo de dato: {type(edad)}')