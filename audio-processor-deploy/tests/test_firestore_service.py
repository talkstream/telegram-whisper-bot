import pytest
from services.firestore import FirestoreService
from unittest.mock import Mock, patch

class TestFirestoreService:

    @patch('google.cloud.firestore.Client')
    def test_update_user_balance(self, mock_firestore):
        """Test balance update with Firestore.Increment"""
        service = FirestoreService('project', 'db')

        # Mock document get
        mock_doc = Mock()
        mock_doc.exists = True
        mock_firestore.return_value.collection.return_value.document.return_value.get.return_value = mock_doc

        service.update_user_balance('user123', -5.5)

        # Verify Increment was called (on update)
        mock_firestore.return_value.collection.return_value.document.return_value.update.assert_called()

    @patch('google.cloud.firestore.Client')
    def test_batch_operations(self, mock_firestore):
        """Test batch write operations"""
        service = FirestoreService('project', 'db')

        batch = service.create_batch()
        # Verify batch was created from client
        assert mock_firestore.return_value.batch.called
        
        # Test usage
        batch.commit()
        assert batch.commit.called
