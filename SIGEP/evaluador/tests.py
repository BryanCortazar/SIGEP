from django.test import TestCase

# Create your tests here.
from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from administrador.models import Evento
from .models import (
    EvaluacionAsignacion,
    EvaluacionEntrega,
    EvaluacionProyecto,
    EvaluacionRespuestaCriterio,
    Rubrica,
    RubricaCriterio,
)


class FormularioEvaluacionIncompletoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.evaluador = User.objects.create_user(
            username="evaluador.cp.eval.006",
            email="evaluador.cp.eval.006@sigep.test",
            password="PruebaSegura123*",
            first_name="Evaluador",
            last_name="Prueba",
            rol="EVAL",
        )
        self.client.force_login(self.evaluador)

        self.evento = Evento.objects.create(
            titulo="Evento CP-EVAL-006",
            descripcion="Evento de prueba",
            fecha=date.today(),
            lugar="Aula 1",
            cupo=100,
            estado="PUBLICADO",
        )
        self.proyecto = EvaluacionProyecto.objects.create(
            evento=self.evento,
            titulo="Proyecto CP-EVAL-006",
            ponente="Responsable de prueba",
            inicio=time(8, 0),
            fin=time(8, 30),
            lugar="H15",
        )
        self.asignacion = EvaluacionAsignacion.objects.create(
            proyecto=self.proyecto,
            evaluador=self.evaluador,
        )
        self.rubrica = Rubrica.objects.create(
            evento=self.evento,
            proyecto=self.proyecto,
            titulo="Rúbrica CP-EVAL-006",
            estado=Rubrica.ESTADO_ACTIVA,
        )
        self.criterio_1 = RubricaCriterio.objects.create(
            rubrica=self.rubrica,
            titulo="Claridad",
            descripcion="Claridad del proyecto",
            puntaje_max=5,
            orden=1,
        )
        self.criterio_2 = RubricaCriterio.objects.create(
            rubrica=self.rubrica,
            titulo="Innovación",
            descripcion="Nivel de innovación",
            puntaje_max=5,
            orden=2,
        )
        self.url = reverse("evaluador:formulario", args=[self.proyecto.id])

    def test_envio_final_incompleto_no_se_registra_como_enviado(self):
        response = self.client.post(
            self.url,
            {
                "accion": "enviar",
                f"valor_{self.criterio_1.id}": "5",
                f"observacion_{self.criterio_1.id}": "Cumple correctamente.",
                # Falta valor y observación del criterio 2.
                "observaciones_generales": "Observación general de prueba.",
            },
        )

        self.assertEqual(response.status_code, 200)

        entrega = EvaluacionEntrega.objects.get(asignacion=self.asignacion)
        self.assertNotEqual(entrega.estado, EvaluacionEntrega.ESTADO_ENVIADA)
        self.assertFalse(
            EvaluacionRespuestaCriterio.objects.filter(
                entrega=entrega,
                criterio=self.criterio_1,
            ).exists()
        )

        mensajes = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("No se puede enviar la evaluación" in mensaje for mensaje in mensajes))

    def test_envio_final_sin_observaciones_generales_no_se_registra_como_enviado(self):
        response = self.client.post(
            self.url,
            {
                "accion": "enviar",
                f"valor_{self.criterio_1.id}": "5",
                f"observacion_{self.criterio_1.id}": "Cumple correctamente.",
                f"valor_{self.criterio_2.id}": "4",
                f"observacion_{self.criterio_2.id}": "Aceptable.",
                "observaciones_generales": "",
            },
        )

        self.assertEqual(response.status_code, 200)

        entrega = EvaluacionEntrega.objects.get(asignacion=self.asignacion)
        self.assertNotEqual(entrega.estado, EvaluacionEntrega.ESTADO_ENVIADA)

        mensajes = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("observaciones generales" in mensaje.lower() for mensaje in mensajes))
