import streamlit as st
import pandas as pd
import numpy as np

st.title('hola mundo loco')

# Solicitar un entero con un paso de 1
edad = st.number_input('Introduce tu edad:', min_value=0, max_value=120, value=25, step=1)

st.write(f'La edad ingresada es: {edad}')
st.write(f'Tipo de dato: {type(edad)}')

st.title("Mi primer gráfico en Streamlit")

# Creamos datos ficticios: una tabla con 20 filas y 3 columnas
chart_data = pd.DataFrame(
    np.random.randn(20, 3),
    columns=['Ventas', 'Gastos', 'Beneficios']
)

# Mostramos el gráfico de líneas
st.line_chart(chart_data)